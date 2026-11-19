import { existsSync, readFileSync, realpathSync } from 'node:fs';
import { homedir } from 'node:os';
import { createRequire } from 'node:module';
import { delimiter, dirname, join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

const fail = (reason, status = null) => { const error = new Error(reason); error.reason = reason; error.status = status; throw error; };
const findSdk = () => {
  const starts = [];
  if (process.env.MDCOMPARE_PI_SDK_DIR) starts.push(process.env.MDCOMPARE_PI_SDK_DIR);
  if (process.env.PI_PACKAGE_DIR) starts.push(process.env.PI_PACKAGE_DIR);
  for (const dir of (process.env.PATH || '').split(delimiter)) {
    const executable = join(dir, process.platform === 'win32' ? 'pi.exe' : 'pi');
    if (existsSync(executable)) starts.push(dirname(realpathSync(executable)));
  }
  for (const start of starts) {
    let dir = resolve(start);
    while (dir !== dirname(dir)) {
      const pkg = join(dir, 'package.json');
      if (existsSync(pkg)) {
        try { if (JSON.parse(readFileSync(pkg, 'utf8')).name === '@earendil-works/pi-coding-agent') return { dir, entry: join(dir, 'dist/index.js') }; }
        catch { /* keep searching parent package roots */ }
      }
      dir = dirname(dir);
    }
  }
  fail('sdk_unavailable');
};
const authUrls = new Set([
  'https://auth.openai.com/api/accounts/oauth/token',
  'https://auth.openai.com/oauth/token',
]);
const requestUrl = input => new URL(typeof input === 'string' || input instanceof URL ? input : input.url);
const connection = entry => {
  const proxy = process.env.HTTPS_PROXY || process.env.https_proxy;
  if (!proxy) return { fetch: (input, init) => globalThis.fetch(input, { ...init, redirect: 'error' }) };
  try {
    const { fetch, ProxyAgent } = createRequire(entry)('undici');
    const dispatcher = new ProxyAgent(proxy);
    return {
      fetch: async (input, init) => {
        const prior = typeof input === 'object' && !(input instanceof URL)
          ? { method: input.method, headers: input.headers, signal: input.signal,
              body: init?.body === undefined && input.body ? await input.clone().arrayBuffer() : undefined }
          : {};
        return fetch(requestUrl(input), { ...prior, ...init, redirect: 'error', dispatcher });
      },
      close: () => dispatcher.destroy(),
    };
  } catch {
    fail('proxy_unavailable');
  }
};
const responseFormat = JSON.parse(readFileSync(new URL('./semantic_score_format.json', import.meta.url), 'utf8'));
const main = async () => {
  const request = JSON.parse(readFileSync(0, 'utf8'));
  const [provider, modelId, ...extra] = request.model.split('/');
  if (extra.length || !['openai', 'openai-codex'].includes(provider) || !modelId) fail('unsupported_model');
  if (!['low', 'medium', 'high', 'xhigh'].includes(request.effort)) fail('unsupported_effort');
  const expected = provider === 'openai' ? 'https://api.openai.com/v1/responses' : 'https://chatgpt.com/backend-api/codex/responses';
  const api = provider === 'openai' ? 'openai-responses' : 'openai-codex-responses';
  const { entry } = findSdk();
  const { ModelRuntime, readStoredCredential } = await import(pathToFileURL(entry).href);
  const agentDir = resolve(process.env.PI_CODING_AGENT_DIR || join(homedir(), '.pi', 'agent'));
  delete process.env.OPENAI_API_KEY;
  delete process.env.CODEX_API_KEY;
  const doctor = request.op === 'doctor';
  const authPath = join(agentDir, 'auth.json');
  const transport = connection(entry);
  const nativeFetch = globalThis.fetch;
  if (doctor) globalThis.fetch = () => fail('unsupported_transport');
  else if (transport.close) globalThis.fetch = (input, init) => {
    if (!authUrls.has(requestUrl(input).href)) fail('unsupported_transport');
    return transport.fetch(input, init);
  };
  let runtime, attempted = false, status = null, usage = null;
  try {
    if (doctor && typeof readStoredCredential !== 'function') fail('sdk_unavailable', null);
    // Avoid creating or locking live auth storage, and never check/refresh a live credential.
    const options = doctor
      ? { refreshOnCreate: false, credentials: { read: async () => undefined, list: async () => [], modify: async () => { throw new Error('read only'); }, delete: async () => { throw new Error('read only'); } } }
      : { authPath };
    runtime = await ModelRuntime.create({ ...options, modelsPath: join(agentDir, 'models.json'), allowModelNetwork: false });
    const model = runtime.getPhysicalModel(provider, modelId);
    if (!model || model.id !== modelId || model.provider !== provider || model.api !== api || !model.reasoning || (model.thinkingLevelMap && !model.thinkingLevelMap[request.effort])) fail('unsupported_model');
    if (doctor) {
      const credential = readStoredCredential(provider, authPath);
      if (credential?.type !== 'oauth' || !credential.access || !credential.refresh || !Number.isFinite(credential.expires)) fail('oauth_required');
      return { ok: true, node: process.versions.node, oauth: credential.expires <= Date.now() ? 'expired' : 'stored' };
    }
    if (!runtime.isUsingOAuth(provider) || (await runtime.checkAuth(provider, {env: {}}))?.type !== 'oauth') fail('oauth_required');
    const reply = await runtime.completeSimple(model, {systemPrompt: request.prompt, messages: [{role: 'user', content: [{type: 'text', text: request.evidence}], timestamp: Date.now()}]}, {
      maxTokens: 4096, reasoning: request.effort, maxRetries: 0, timeoutMs: 170000, transport: 'sse', cacheRetention: 'none', env: {},
      fetch: async (input, init) => {
        if (requestUrl(input).href !== expected || attempted) fail('unsupported_transport');
        attempted = true;
        const response = await transport.fetch(input, init);
        status = response.status;
        return response;
      },
      onPayload: payload => ({...payload, max_output_tokens: 4096, text: {...payload.text, format: responseFormat}}),
    });
    const counts = [reply?.usage?.input, reply?.usage?.output, reply?.usage?.cacheRead, reply?.usage?.cacheWrite];
    if (counts.every(n => Number.isSafeInteger(n) && n >= 0)) usage = counts.reduce((sum, n) => sum+n, 0);
    if (attempted && (!status || status >= 500 || (status < 400 && ['aborted', 'error'].includes(reply?.stopReason)))) fail('unknown_outcome', status);
    if (status >= 400) fail('provider_error', status);
    if (!attempted || reply?.stopReason !== 'stop') fail('invalid_response');
    const text = reply.content.filter(item => item.type === 'text').map(item => item.text).join('');
    return {ok: true, reply: text, usage_tokens: usage};
  } catch (error) {
    const reason = status === 429 ? 'rate_limited'
      : status === 401 || status === 403 ? 'auth_error'
      : error.reason || (attempted ? 'unknown_outcome' : 'bridge_error');
    return {ok: false, reason, http_status: status, usage_tokens: usage};
  } finally {
    try { if (runtime && !doctor) await runtime.settleCredentialWrites(); }
    finally { globalThis.fetch = nativeFetch; if (transport.close) await transport.close(); }
  }
};
try { process.stdout.write(JSON.stringify(await main())); }
catch (error) { process.stdout.write(JSON.stringify({ok: false, reason: error.reason || 'unknown_outcome'})); }
