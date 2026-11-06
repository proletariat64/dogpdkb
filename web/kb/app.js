const state = { data: null, group: 'all', query: '', sort: 'recent', visible: 12 };
const number = new Intl.NumberFormat('zh-CN');
const $ = (id) => document.getElementById(id);

function text(tag, className, value) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  element.textContent = value;
  return element;
}

function setText(id, value) {
  $(id).textContent = value;
}

function openDetail(type, title) {
  setText('detail-type', type);
  setText('detail-title', title);
  $('detail-content').replaceChildren();
  $('detail').showModal();
}

function addFacts(facts) {
  const grid = document.createElement('div');
  grid.className = 'detail-facts';
  facts.forEach(([label, value]) => {
    const item = document.createElement('div');
    item.append(text('span', '', label), text('strong', '', value));
    grid.append(item);
  });
  $('detail-content').append(grid);
}

function showTopic(topic) {
  openDetail('KNOWLEDGE COMMUNITY', topic.name);
  addFacts([['Community', `#${topic.id}`], ['Connected nodes', number.format(topic.count)], ['Knowledge domain', topic.domain]]);
  $('detail-content').append(text('p', '', topic.description));
  $('detail-content').append(text('h3', '', '代表性知识节点'));
  const labels = document.createElement('div');
  labels.className = 'detail-labels';
  topic.labels.forEach((label) => labels.append(text('span', 'tag', label)));
  $('detail-content').append(labels);
  const actions = document.createElement('div');
  actions.className = 'detail-actions';
  const search = text('button', 'text-button', '检索相关文档 →');
  search.type = 'button';
  search.addEventListener('click', () => {
    $('detail').close();
    $('search').value = topic.name;
    state.query = topic.name;
    state.visible = 12;
    renderDocuments();
    $('documents').scrollIntoView({ behavior: 'smooth' });
  });
  actions.append(search);
  $('detail-content').append(actions);
}

function showDocument(doc) {
  openDetail(`SOURCE DOCUMENT${doc.id ? ` / ${doc.id}` : ''}`, doc.title);
  addFacts([['Knowledge domain', doc.group], ['Captured at', doc.date || '未标注'], ['Document ID', doc.id || '未标注']]);
  if (doc.concepts.length) {
    $('detail-content').append(text('h3', '', '图谱提取的产品概念'));
    const labels = document.createElement('div');
    labels.className = 'detail-labels';
    doc.concepts.forEach((label) => labels.append(text('span', 'tag', label)));
    $('detail-content').append(labels);
  }
  $('detail-content').append(text('h3', '', '本地语料路径'));
  $('detail-content').append(text('div', 'detail-path', `graphify_flatten_ready/${doc.file}`));
  const actions = document.createElement('div');
  actions.className = 'detail-actions';
  const source = text('a', 'primary', '打开语雀源库 ↗');
  source.href = state.data.book.url;
  source.target = '_blank';
  source.rel = 'noopener';
  actions.append(source, text('span', '', '可按标题或文档 ID 在源库中定位原文'));
  $('detail-content').append(actions);
}

function renderTopics() {
  const grid = $('topic-grid');
  grid.replaceChildren();
  state.data.topics.forEach((topic, index) => {
    const card = document.createElement('button');
    card.type = 'button';
    card.className = 'topic-card';
    const top = document.createElement('div');
    top.className = 'card-top';
    top.append(text('span', 'glyph', String(index + 1).padStart(2, '0')), text('span', 'tag', `${number.format(topic.count)} nodes`));
    const meta = document.createElement('div');
    meta.className = 'topic-meta';
    meta.append(text('span', '', topic.domain), text('span', '', '查看主题 →'));
    card.append(top, text('h3', '', topic.name), text('p', '', topic.description), meta);
    card.addEventListener('click', () => showTopic(topic));
    grid.append(card);
  });
}

function filteredDocuments() {
  const query = state.query.trim().toLocaleLowerCase('zh-CN');
  const docs = state.data.documents.filter((doc) => {
    if (state.group !== 'all' && doc.group !== state.group) return false;
    if (!query) return true;
    return `${doc.title}\n${doc.file}\n${doc.concepts.join('\n')}`.toLocaleLowerCase('zh-CN').includes(query);
  });
  return docs.sort((a, b) => state.sort === 'title'
    ? a.title.localeCompare(b.title, 'zh-CN')
    : (b.date || '').localeCompare(a.date || '') || a.title.localeCompare(b.title, 'zh-CN'));
}

function renderDocuments() {
  const docs = filteredDocuments();
  const shown = docs.slice(0, state.visible);
  const grid = $('document-grid');
  grid.replaceChildren();
  shown.forEach((doc) => {
    const card = document.createElement('article');
    card.className = 'document-card';
    const top = document.createElement('div');
    top.className = 'card-top';
    top.append(text('span', 'glyph', doc.group.slice(0, 1)), text('span', 'tag', doc.group));
    card.append(top, text('h3', '', doc.title), text('p', 'document-path', doc.file));
    if (doc.concepts.length) {
      const concepts = document.createElement('div');
      concepts.className = 'concepts';
      doc.concepts.slice(0, 3).forEach((label) => concepts.append(text('span', 'tag', label)));
      card.append(concepts);
    }
    const bottom = document.createElement('div');
    bottom.className = 'card-bottom';
    const detail = text('button', '', '查看详情 →');
    detail.type = 'button';
    detail.addEventListener('click', () => showDocument(doc));
    bottom.append(text('span', '', doc.date || '日期未标注'), detail);
    card.append(bottom);
    grid.append(card);
  });
  setText('results', `${number.format(docs.length)} 篇匹配文档 · 当前显示 ${number.format(shown.length)} 篇`);
  $('empty').hidden = docs.length !== 0;
  $('load-more').hidden = shown.length >= docs.length;
  $('load-more').textContent = `加载更多文档（剩余 ${number.format(docs.length - shown.length)} 篇）`;
}

async function init() {
  try {
    const response = await fetch('catalog.json');
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    state.data = await response.json();
    const { stats } = state.data;
    setText('document-count', number.format(stats.documents));
    setText('node-count', number.format(stats.nodes));
    setText('edge-count', number.format(stats.edges));
    setText('community-count', number.format(stats.communities));
    setText('document-total', number.format(stats.documents));
    setText('topic-total', number.format(state.data.topics.length));
    setText('snapshot-label', `${number.format(stats.documents)} 篇文档已连接`);
    setText('generated-at', `Snapshot ${state.data.generated_at.slice(0, 10)}`);
    $('global-status').hidden = true;
    const initialQuery = new URLSearchParams(window.location.search).get('q');
    if (initialQuery) {
      $('search').value = initialQuery;
      state.query = initialQuery;
    }
    renderTopics();
    renderDocuments();
  } catch (error) {
    setText('global-status', `知识快照加载失败：${error.message}`);
    $('global-status').classList.add('error');
    setText('results', '文档目录暂不可用');
  }
}

document.querySelectorAll('[data-group]').forEach((button) => {
  button.addEventListener('click', () => {
    document.querySelectorAll('[data-group]').forEach((item) => item.classList.remove('selected'));
    button.classList.add('selected');
    state.group = button.dataset.group;
    state.visible = 12;
    renderDocuments();
  });
});
$('search').addEventListener('input', (event) => {
  state.query = event.target.value;
  state.visible = 12;
  renderDocuments();
});
$('sort').addEventListener('change', (event) => {
  state.sort = event.target.value;
  state.visible = 12;
  renderDocuments();
});
$('load-more').addEventListener('click', () => {
  state.visible += 12;
  renderDocuments();
});
$('detail-close').addEventListener('click', () => $('detail').close());
document.addEventListener('keydown', (event) => {
  if (event.key === '/' && !['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement.tagName)) {
    event.preventDefault();
    $('search').focus();
  }
});
init();
