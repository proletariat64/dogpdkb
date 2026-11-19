import { readFile } from 'node:fs/promises';
import { existsSync, readFileSync, realpathSync } from 'node:fs';
import { homedir } from 'node:os';
import { createRequire } from 'node:module';
import { delimiter, dirname, join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

const schema = {
  type: 'object', additionalProperties: false, required: ['images'],
  properties: { images: { type: 'array', items: {
    type: 'object', additionalProperties: false,
    required: ['id', 'kind', 'markdown', 'uncertainty'],
    properties: { id: { type: 'string' }, kind: { type: 'string', enum: ['mermaid', 'table', 'ui', 'text', 'mixed', 'hold'] }, markdown: { type: 'string' }, uncertainty: { type: 'string' } },
  } } },
};
const format = { type: 'json_schema', name: 'mdocr_image_patches_1', strict: true, schema };
const fail = (reason, message, status = null) => { const e = new Error(message); e.reason = reason; e.status = status; throw e; };
const selectParser = async () => {
  const root = process.env.MDOCR_NODE_DEPENDENCIES;
  if (!root) fail('mermaid_parser_unavailable', 'Set MDOCR_NODE_DEPENDENCIES to the npm prefix containing mermaid@12.3.0 and jsdom@26.1.0');
  try {
    const load = createRequire(join(resolve(root), 'package.json'));
    const { JSDOM } = load('jsdom');
    const dom = new JSDOM();
    globalThis.window = dom.window;
    globalThis.document = dom.window.document;
    const parser = (await import(pathToFileURL(load.resolve('mermaid')).href)).default;
    parser.initialize({ startOnLoad: false, securityLevel: 'strict', suppressErrorRendering: true });
    return parser;
  } catch {
    fail('mermaid_parser_unavailable', 'Cannot load Mermaid and jsdom from MDOCR_NODE_DEPENDENCIES');
  }
};
const fences = markdown => {
  const blocks = [];
  let open;
  for (const line of markdown.split(/\r?\n/)) {
    const fence = /^ {0,3}(`{3,}|~{3,})(.*)$/.exec(line);
    if (!open) {
      if (fence && (fence[1][0] !== '`' || !fence[2].includes('`'))) {
        open = { marker: fence[1][0], width: fence[1].length, info: fence[2].trim(), lines: [] };
      }
    } else if (fence && fence[1][0] === open.marker && fence[1].length >= open.width && !fence[2].trim()) {
      blocks.push({ ...open, closed: true });
      open = undefined;
    } else open.lines.push(line);
  }
  if (open) blocks.push({ ...open, closed: false });
  return blocks;
};
const cells = line => {
  const values = [''];
  let slashes = 0;
  for (const ch of line.trim()) {
    if (ch === '|' && slashes % 2 === 0) values.push('');
    else values[values.length - 1] += ch;
    slashes = ch === '\\' ? slashes + 1 : 0;
  }
  if (values.length > 1 && values[0] === '') values.shift();
  if (values.length > 1 && values.at(-1) === '' && line.trim().endsWith('|')) values.pop();
  return values;
};
const validateTables = patches => {
  for (const {kind, markdown} of patches) {
    if (!['mixed', 'table'].includes(kind)) continue;
    if (/<\/?(?:table|thead|tbody|tfoot|tr|th|td|caption|colgroup|col)\b/i.test(markdown)) fail('invalid_response', 'Provider returned an HTML table');
    if (kind !== 'table') continue;
    const tables = markdown.trim().split(/\r?\n[ \t]*\r?\n/);
    for (const table of tables) {
      const lines = table.split(/\r?\n/);
      if (lines.length < 2 || lines.some(line => !line.includes('|') || /^ {4}|^\t/.test(line))) fail('invalid_response', 'Provider did not return a Markdown table');
      const [heading, separators, ...rows] = lines.map(cells);
      if (!separators.every(cell => /^:?-+:?$/.test(cell.trim())) || [heading, ...rows].some(row => row.length !== separators.length)) fail('invalid_response', 'Provider returned inconsistent Markdown table columns');
    }
  }
};
const validateMermaid = async patches => {
  const blocks = patches.flatMap(p => {
    const diagrams = fences(p.markdown).filter(block => /^mermaid(?:\s|$)/i.test(block.info));
    if ((p.kind === 'mermaid' && !diagrams.length) || diagrams.some(block => !block.closed)) fail('invalid_response', 'Provider returned an incomplete Mermaid block');
    return diagrams.map(block => block.lines.join('\n'));
  });
  if (!blocks.length) return;
  const parser = await selectParser();
  for (const block of blocks) {
    try { await parser.parse(block); }
    catch { fail('invalid_response', 'Provider returned syntactically invalid Mermaid'); }
  }
};
const findSdk = () => {
  const starts = [];
  if (process.env.MDOCR_PI_SDK_DIR) starts.push(process.env.MDOCR_PI_SDK_DIR);
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
  fail('sdk_unavailable', 'Could not locate the installed Pi coding-agent SDK; set PI_PACKAGE_DIR to the Pi package root');
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
    fail('proxy_unavailable', 'Could not configure the installed Pi HTTPS proxy transport');
  }
};
const main = async () => {
  const request = JSON.parse(await new Promise((resolve, reject) => {
    let data = ''; process.stdin.setEncoding('utf8'); process.stdin.on('data', x => data += x);
    process.stdin.on('end', () => resolve(data)); process.stdin.on('error', reject);
  }));
  const [provider, modelId, ...extra] = request.model.split('/');
  if (extra.length || !['openai', 'openai-codex'].includes(provider) || !modelId) fail('unsupported_model', 'Model must use an allowlisted provider/model route');
  if (!['low', 'medium', 'high', 'xhigh'].includes(request.effort)) fail('unsupported_effort', 'Unsupported reasoning effort');
  const { entry } = findSdk();
  const { ModelRuntime } = await import(pathToFileURL(entry).href);
  const agentDir = resolve(process.env.PI_CODING_AGENT_DIR || join(homedir(), '.pi', 'agent'));
  // Prevent ambient keys from taking precedence on the request path; Pi owns the stored OAuth bearer and refresh.
  delete process.env.OPENAI_API_KEY;
  delete process.env.CODEX_API_KEY;
  const transport = connection(entry);
  const nativeFetch = globalThis.fetch;
  if (transport.close) {
    globalThis.fetch = (input, init) => {
      if (!authUrls.has(requestUrl(input).href)) fail('unsupported_transport', 'Unexpected Pi authentication route');
      return transport.fetch(input, init);
    };
  }
  let runtime;
  try {
    runtime = await ModelRuntime.create({ authPath: join(agentDir, 'auth.json'), modelsPath: join(agentDir, 'models.json'), allowModelNetwork: false });
    const model = runtime.getPhysicalModel(provider, modelId);
    if (!model || model.provider !== provider || model.id !== modelId) fail('unsupported_model', `Unknown physical Pi model ${request.model}`);
    if (!model.input?.includes('image')) fail('unsupported_model', 'Selected Pi model does not advertise image input');
    if (!model.reasoning || (model.thinkingLevelMap && !model.thinkingLevelMap[request.effort])) fail('unsupported_effort', `Model ${request.model} does not support ${request.effort} effort`);
    if (!runtime.isUsingOAuth(provider)) fail('oauth_required', `Pi OAuth credentials are required for ${provider}`);
    const authStatus = await runtime.checkAuth(provider, { env: {} });
    if (!authStatus || authStatus.type !== 'oauth') fail('oauth_required', `Pi OAuth credentials are required for ${provider}`);
    const expected = provider === 'openai' ? 'https://api.openai.com/v1/responses' : 'https://chatgpt.com/backend-api/codex/responses';
    const expectedApi = provider === 'openai' ? 'openai-responses' : 'openai-codex-responses';
    if (model.api !== expectedApi) fail('unsupported_model', 'Pi route does not use the supported Responses transport');
    if (request.op === 'preflight') return { ok: true, model: request.model, effort: request.effort };
    if (request.op !== 'convert') fail('bridge_error', 'Unknown bridge operation');
    const content = [{ type: 'text', text: `${request.markdown}\n\nStable image occurrences are named in the source.` }];
    for (const image of request.images) {
      const bytes = await readFile(image.path);
      const ext = image.path.toLowerCase().split('.').pop();
      const mimeType = ({ png: 'image/png', jpg: 'image/jpeg', jpeg: 'image/jpeg', webp: 'image/webp', gif: 'image/gif' })[ext];
      if (!mimeType) fail('invalid_image', `Unsupported image format for ${image.id}`);
      content.push({ type: 'text', text: `Original image occurrence: ${image.id}` }, { type: 'image', mimeType, data: bytes.toString('base64') });
    }
    let attempted = false;
    let status = null;
    const reply = await runtime.completeSimple(model, { systemPrompt: request.prompt, messages: [{ role: 'user', content, timestamp: Date.now() }] }, {
      maxTokens: 8192, samplingParams: { max_output_tokens: 8192 }, reasoning: request.effort,
      maxRetries: 0, timeoutMs: 230000, transport: 'sse', cacheRetention: 'none', env: {},
      fetch: async (input, init) => {
        const url = requestUrl(input);
        if (url.href !== expected || attempted) fail('unsupported_transport', 'Pi attempted an unauthorized route or duplicate submission');
        attempted = true;
        const response = await transport.fetch(input, init);
        status = response.status;
        return response;
      },
      onPayload: payload => ({ ...payload, max_output_tokens: 8192, text: { ...payload.text, format } }),
    });
    const usage = reply?.usage;
    const usageTokens = usage && [usage.input, usage.output, usage.cacheRead, usage.cacheWrite].every(n => Number.isSafeInteger(n) && n >= 0)
      ? usage.input + usage.output + usage.cacheRead + usage.cacheWrite : null;
    try {
      if (status && status >= 400) {
        if (status === 429) fail('rate_limited', 'Provider rate limited the request', status);
        if ([401, 403].includes(status)) fail('auth_error', 'Provider rejected authentication', status);
        fail(status >= 500 ? 'unknown_outcome' : 'provider_error', 'Provider did not accept the request', status);
      }
      if (attempted && (!status || ['error', 'aborted'].includes(reply.stopReason))) fail('unknown_outcome', 'Provider outcome is unknown');
      if (!attempted || reply.stopReason !== 'stop') fail('invalid_response', 'Provider did not complete the structured response');
      const text = reply.content.filter(x => x.type === 'text').map(x => x.text).join('');
      let parsed;
      try { parsed = JSON.parse(text); } catch { fail('invalid_response', 'Provider returned invalid JSON'); }
      const ids = request.images.map(x => x.id);
      if (!Array.isArray(parsed?.images) || JSON.stringify(parsed.images.map(x => x?.id)) !== JSON.stringify(ids)) fail('invalid_response', 'Response IDs do not match image order');
      for (const patch of parsed.images) {
        if (typeof patch.markdown !== 'string' || typeof patch.uncertainty !== 'string' || !schema.properties.images.items.properties.kind.enum.includes(patch.kind)) fail('invalid_response', 'Provider returned an invalid image patch');
      }
      validateTables(parsed.images);
      await validateMermaid(parsed.images);
      return { ok: true, patches: parsed.images, usage_tokens: usageTokens };
    } catch (error) {
      error.usageTokens = usageTokens;
      throw error;
    }
  } finally {
    try { if (runtime) await runtime.settleCredentialWrites(); }
    finally {
      globalThis.fetch = nativeFetch;
      if (transport.close) await transport.close();
    }
  }
};
try { process.stdout.write(JSON.stringify(await main())); }
catch (error) {
const status = Number.isInteger(error.status) ? error.status : null;
const reason = error.reason || (status === 429 ? 'rate_limited' : status === 401 || status === 403 ? 'auth_error' : 'provider_error');
const message = status === 429 ? 'Provider rate limited the request' : status === 401 || status === 403 ? 'Provider rejected authentication' : reason === 'unknown_outcome' ? 'Provider request failed after possible submission; outcome is unknown' : reason === 'invalid_response' || reason.startsWith('unsupported_') || reason === 'oauth_required' || reason === 'sdk_unavailable' || reason === 'mermaid_parser_unavailable' || reason === 'invalid_image' || reason === 'proxy_unavailable' ? error.message : 'Pi provider request failed; details withheld';
process.stdout.write(JSON.stringify({ ok: false, error: { reason, message, status, usage_tokens: error.usageTokens ?? null } })); process.exitCode = 1;
}
