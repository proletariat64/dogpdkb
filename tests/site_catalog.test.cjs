const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const script = fs.readFileSync(path.join(__dirname, '../web/kb/app.js'), 'utf8');
const catalog = {
  stats: { documents: 2, nodes: 0, edges: 0, communities: 0 },
  topics: [], generated_at: '2026-11-06',
  documents: ['Alpha', 'Beta'].map(title => ({
    title, file: `${title}.md`, concepts: [], group: 'funds', date: '',
  })),
};
function load() {
  const elements = new Map();
  function element() {
    return { textContent: '', children: [], listeners: {}, dataset: {},
      classList: { add() {}, remove() {} },
      append(...children) { this.children.push(...children); },
      replaceChildren(...children) { this.children = children; },
      addEventListener(event, callback) { this.listeners[event] = callback; },
    };
  }
  const group = element();
  group.dataset.group = 'funds';
  const document = {
    getElementById(id) {
      if (!elements.has(id)) elements.set(id, element());
      return elements.get(id);
    },
    createElement: element, querySelectorAll: () => [group], addEventListener() {},
  };
  document.getElementById('results').textContent = 'Loading';
  let resolve, reject;
  const pending = new Promise((yes, no) => { resolve = yes; reject = no; });
  vm.runInNewContext(script, {
    document, window: { location: { search: '' } }, URLSearchParams,
    fetch: () => pending,
  });
  function interact() {
    elements.get('search').listeners.input({ target: { value: 'Beta' } });
    group.listeners.click();
    elements.get('sort').listeners.change({ target: { value: 'title' } });
    elements.get('load-more').listeners.click();
  }
  return { elements, interact, resolve, reject };
}
const settle = () => new Promise(resolve => setImmediate(resolve));

test('pending catalog interactions retain filters and render after loading', async () => {
  const page = load();
  assert.doesNotThrow(page.interact);
  assert.equal(page.elements.get('results').textContent, 'Loading');
  page.resolve({ ok: true, json: async () => catalog });
  await settle();
  assert.match(page.elements.get('results').textContent, /^1 篇匹配文档/);
  const cards = page.elements.get('document-grid').children;
  assert.equal(cards.length, 1);
  assert.equal(cards[0].children[1].textContent, 'Beta');
});

for (const failure of ['network', 'http']) {
  test(`${failure} failure keeps error visible during later interactions`, async () => {
    const page = load();
    if (failure === 'network') page.reject(new Error('offline'));
    else page.resolve({ ok: false, status: 503 });
    await settle();
    const message = page.elements.get('global-status').textContent;
    assert.match(message, /知识快照加载失败/);
    assert.doesNotThrow(page.interact);
    assert.equal(page.elements.get('results').textContent, '文档目录暂不可用');
    assert.equal(page.elements.get('global-status').textContent, message);
  });
}
