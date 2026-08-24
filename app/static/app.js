// three.js（同梱）はプレビュー表示時に遅延読み込みする。
// 万一読み込めなくてもチャットとダウンロードは動作させる。
const APP_VERSION = '0.4.0';

let THREE, STLLoader, OrbitControls;

async function ensureThree() {
  if (THREE) return true;
  try {
    THREE = await import('three');
    ({ STLLoader } = await import('three/addons/loaders/STLLoader.js'));
    ({ OrbitControls } = await import('three/addons/controls/OrbitControls.js'));
    return true;
  } catch {
    return false;
  }
}

const STATUS_LABELS = {
  planning: '相談中',
  modeled: 'モデル生成済み',
  printed: '印刷済み',
};

const state = {
  projects: [],
  currentId: null,
  models: [],
  currentVersion: null,
  canPrint: false,
};

const $ = (id) => document.getElementById(id);

// ---------- API ----------

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  if (!res.ok) {
    let detail = `エラーが発生しました (HTTP ${res.status})`;
    try {
      const body = await res.json();
      if (body.detail) detail = body.detail;
    } catch { /* JSONでないレスポンスはそのまま */ }
    throw new Error(detail);
  }
  return res.json();
}

// ---------- プロジェクト一覧 ----------

async function refreshProjects() {
  const data = await api('/api/projects');
  state.projects = data.projects;
  const ul = $('project-list');
  ul.innerHTML = '';
  for (const p of state.projects) {
    const li = document.createElement('li');
    li.className = p.id === state.currentId ? 'active' : '';
    const title = document.createElement('span');
    title.className = 'title';
    title.textContent = p.title;
    const chip = document.createElement('span');
    chip.className = `status-chip status-${p.status}`;
    chip.textContent = STATUS_LABELS[p.status] ?? p.status;
    li.append(title, chip);
    li.onclick = () => openProject(p.id);
    ul.appendChild(li);
  }
  renderRecentProjects();
}

function projectGlyph(title, index) {
  if (/ケーブル|コード|クリップ/.test(title)) return '⌁';
  if (/ペン|収納|ケース/.test(title)) return '▥';
  if (/スタンド|台|ホルダー/.test(title)) return '◩';
  if (/フック|掛け|バイザー/.test(title)) return '∩';
  return ['◇', '◫', '⌂'][index % 3];
}

function renderRecentProjects() {
  const container = $('recent-projects');
  const toggle = $('all-projects-btn');
  container.innerHTML = '';

  if (state.projects.length === 0) {
    const empty = document.createElement('p');
    empty.className = 'recent-empty';
    empty.textContent = 'まだプロジェクトがありません。最初のアイデアをAIに話してみましょう。';
    container.appendChild(empty);
    toggle.classList.add('hidden');
    $('recent-dots').innerHTML = '';
    return;
  }

  toggle.classList.remove('hidden');
  toggle.firstChild.textContent = 'すべて見る ';
  const projects = state.projects;

  projects.forEach((project, index) => {
    const card = document.createElement('button');
    card.type = 'button';
    card.className = 'project-card';
    card.onclick = () => openProject(project.id);

    const visual = document.createElement('span');
    visual.className = 'project-visual';
    const glyph = document.createElement('span');
    glyph.className = 'project-glyph';
    glyph.textContent = projectGlyph(project.title, index);
    visual.appendChild(glyph);

    const body = document.createElement('span');
    body.className = 'project-card-body';
    const title = document.createElement('span');
    title.className = 'project-card-title';
    title.textContent = project.title;
    const status = document.createElement('span');
    status.className = `status-chip status-${project.status}`;
    status.textContent = STATUS_LABELS[project.status] ?? project.status;
    body.append(title, status);

    card.append(visual, body);
    container.appendChild(card);
  });
  requestAnimationFrame(updateRecentDots);
}

function updateRecentDots() {
  const container = $('recent-projects');
  const dots = $('recent-dots');
  const maxScroll = container.scrollWidth - container.clientWidth;
  if (maxScroll <= 4) {
    dots.innerHTML = '';
    return;
  }

  const pageCount = Math.min(5, Math.max(2, Math.ceil(container.scrollWidth / container.clientWidth)));
  const active = Math.round((container.scrollLeft / maxScroll) * (pageCount - 1));
  dots.replaceChildren(...Array.from({ length: pageCount }, (_, index) => {
    const dot = document.createElement('span');
    dot.classList.toggle('active', index === active);
    return dot;
  }));
}

async function openProject(id) {
  state.currentId = id;
  const data = await api(`/api/projects/${id}`);
  showChat(data.project);
  renderMessages(data.messages);
  setModels(data.models);
  refreshProjects();
}

function showWelcome() {
  state.currentId = null;
  document.body.classList.add('home-mode');
  document.body.classList.remove('chat-mode');
  $('welcome').classList.remove('hidden');
  $('chat').classList.add('hidden');
  $('preview-panel').classList.add('hidden');
  $('welcome-input').value = '';
  refreshProjects();
}

function showChat(project) {
  document.body.classList.remove('home-mode');
  document.body.classList.add('chat-mode');
  $('welcome').classList.add('hidden');
  $('chat').classList.remove('hidden');
  $('chat-title').textContent = project.title;
  const chip = $('chat-status');
  chip.className = `status-chip status-${project.status}`;
  chip.textContent = STATUS_LABELS[project.status] ?? project.status;
  renderChatProgress(project.status);
  renderChatSummary(project.title);
}

function renderChatProgress(status) {
  const order = ['planning', 'modeled', 'printed'];
  const activeIndex = Math.max(0, order.indexOf(status));
  document.querySelectorAll('#chat-progress .progress-step').forEach((step, index) => {
    step.classList.toggle('active', index === activeIndex);
    step.classList.toggle('complete', index < activeIndex);
  });
  document.querySelectorAll('#chat-progress .progress-line').forEach((line, index) => {
    line.classList.toggle('complete', index < activeIndex);
  });
}

function renderChatSummary(title) {
  const chips = [];
  const dimension = title.match(/\d+(?:\.\d+)?\s*(?:インチ|mm|cm)/i)?.[0]?.replace(/\s/g, '');
  if (dimension) chips.push(dimension.includes('インチ') ? `${dimension}モニター` : dimension);

  const keywordRules = [
    [/モニター|ディスプレイ/, 'モニター'],
    [/クランプ|挟/, 'クランプ式'],
    [/ペン.*(?:ホルダー|立て)|ペンホルダー|ペン立て/, 'ペンホルダー'],
    [/ケーブル|コード/, 'ケーブル'],
    [/フック|壁掛け/, '壁掛けフック'],
    [/スタンド/, 'スタンド'],
    [/バイザー|日除け/, 'バイザー'],
  ];
  for (const [pattern, label] of keywordRules) {
    if (pattern.test(title) && !chips.some((chip) => chip.includes(label) || label.includes(chip))) chips.push(label);
    if (chips.length === 3) break;
  }
  if (chips.length === 0) chips.push('AIと相談中');

  const container = $('chat-summary-chips');
  container.replaceChildren(...chips.slice(0, 3).map((label) => {
    const chip = document.createElement('span');
    chip.textContent = label;
    return chip;
  }));
}

// ---------- チャット ----------

function renderMessages(messages) {
  const box = $('messages');
  box.innerHTML = '';
  let latestAssistant = -1;
  messages.forEach((message, index) => {
    if (message.role === 'assistant') latestAssistant = index;
  });
  messages.forEach((message, index) => {
    appendMessage(message.role, message.content, index === latestAssistant);
  });
  requestAnimationFrame(scrollConversationToEnd);
}

function appendMessage(role, content, interactive = false) {
  const box = $('messages');
  const row = document.createElement('div');
  row.className = `message-row ${role}`;

  if (role === 'assistant') {
    const avatar = document.createElement('img');
    avatar.className = 'assistant-avatar';
    avatar.src = '/icons/icon-ai-modeling-v3-32.png';
    avatar.alt = 'T-Lab AI';
    row.appendChild(avatar);
  }

  const stack = document.createElement('div');
  stack.className = 'message-stack';
  const parsed = role === 'assistant' ? parseQuestion(content) : null;
  const bubbleText = parsed
    ? [parsed.intro, interactive ? '' : parsed.question].filter(Boolean).join('\n\n')
    : content;
  if (bubbleText) {
    const bubble = document.createElement('div');
    bubble.className = `msg ${role}`;
    bubble.textContent = cleanMarkdown(bubbleText);
    stack.appendChild(bubble);
  }
  if (parsed && interactive) stack.appendChild(createQuestionCard(parsed));
  row.appendChild(stack);
  box.appendChild(row);
  requestAnimationFrame(scrollConversationToEnd);
}

function scrollConversationToEnd() {
  const scroll = document.querySelector('.conversation-scroll');
  if (scroll) scroll.scrollTop = scroll.scrollHeight;
}

function cleanMarkdown(text) {
  return text.replace(/\*\*(.*?)\*\*/g, '$1').trim();
}

function parseQuestion(content) {
  const taggedQuestion = content.match(/\[QUESTION(?:\s+(\d+)\s*\/\s*(\d+))?\]\s*\n?([\s\S]*?)(?=\n\[OPTIONS\])/i);
  const taggedOptions = content.match(/\[OPTIONS\]\s*\n?([^\n]+)/i);
  if (!taggedQuestion || !taggedOptions) return null;

  const options = taggedOptions[1].split('|').map((option) => cleanMarkdown(option)).filter(Boolean);
  if (options.length < 2) return null;
  const intro = content
    .replace(taggedQuestion[0], '')
    .replace(taggedOptions[0], '')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
  return {
    current: Number(taggedQuestion[1] || 1),
    total: Number(taggedQuestion[2] || 3),
    question: cleanMarkdown(taggedQuestion[3]),
    options: options.slice(0, 3),
    intro,
  };
}

function choiceKind(option, index) {
  if (/AI|おまかせ/.test(option)) return 'ai';
  if (/背面|裏|後ろ/.test(option)) return 'back';
  return index === 0 ? 'top' : 'plain';
}

function createQuestionCard(question) {
  const card = document.createElement('section');
  card.className = 'question-card';
  const count = document.createElement('span');
  count.className = 'question-count';
  count.textContent = `質問 ${question.current} / ${question.total}`;
  const heading = document.createElement('h2');
  heading.textContent = question.question;
  const choices = document.createElement('div');
  choices.className = 'question-choices';

  question.options.forEach((option, index) => {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = `question-choice choice-${choiceKind(option, index)}`;
    const visual = document.createElement('span');
    visual.className = 'choice-visual';
    visual.setAttribute('aria-hidden', 'true');
    const label = document.createElement('strong');
    label.textContent = option;
    button.append(visual, label);
    button.onclick = () => {
      $('chat-input').value = option;
      $('chat-form').requestSubmit();
    };
    choices.appendChild(button);
  });

  const helper = document.createElement('p');
  helper.className = 'question-helper';
  helper.textContent = '迷ったら「AIにおまかせ」で、印刷しやすい形を提案します。';
  card.append(count, heading, choices, helper);
  return card;
}

function setLoading(on, text = 'AIが考えています…（1分ほどかかることがあります）') {
  $('loading-text').textContent = text;
  $('loading-overlay').classList.toggle('hidden', !on);
}

async function handleAITurn(promise) {
  setLoading(true);
  try {
    const data = await promise;
    return data;
  } catch (e) {
    appendMessage('error', e.message);
    return null;
  } finally {
    setLoading(false);
  }
}

$('welcome-form').onsubmit = async (e) => {
  e.preventDefault();
  const message = $('welcome-input').value.trim();
  if (!message) return;
  const data = await handleAITurn(
    api('/api/projects', { method: 'POST', body: JSON.stringify({ message }) })
  );
  if (data) await openProject(data.project.id);
};

$('chat-form').onsubmit = async (e) => {
  e.preventDefault();
  const input = $('chat-input');
  const message = input.value.trim();
  if (!message || state.currentId === null) return;
  appendMessage('user', message);
  input.value = '';
  const data = await handleAITurn(
    api(`/api/projects/${state.currentId}/messages`, {
      method: 'POST',
      body: JSON.stringify({ message }),
    })
  );
  if (data) await openProject(state.currentId);
};

// Enterで送信（Shift+Enterで改行）
$('chat-input').addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
    e.preventDefault();
    $('chat-form').requestSubmit();
  }
});

$('new-project-btn').onclick = showWelcome;
$('brand-home').onclick = showWelcome;
$('chat-back').onclick = showWelcome;

document.querySelectorAll('.prompt-chips button').forEach((button) => {
  button.onclick = () => {
    $('welcome-input').value = button.dataset.prompt;
    $('welcome-input').focus();
  };
});

$('all-projects-btn').onclick = () => {
  const container = $('recent-projects');
  const maxScroll = container.scrollWidth - container.clientWidth;
  const atEnd = maxScroll - container.scrollLeft < 8;
  container.scrollTo({ left: atEnd ? 0 : maxScroll, behavior: 'smooth' });
};

$('recent-projects').addEventListener('scroll', () => requestAnimationFrame(updateRecentDots));
window.addEventListener('resize', () => requestAnimationFrame(updateRecentDots));

$('printer-settings-btn').onclick = () => {
  alert('プリンタ設定は .env の BAMBU_IP / BAMBU_ACCESS_CODE / BAMBU_SERIAL を編集し、アプリを再起動すると反映されます。');
};

$('delete-project-btn').onclick = async () => {
  if (state.currentId === null) return;
  if (!confirm('このプロジェクトを削除しますか？')) return;
  await api(`/api/projects/${state.currentId}`, { method: 'DELETE' });
  showWelcome();
};

$('mark-printed').onclick = async () => {
  if (state.currentId === null) return;
  await api(`/api/projects/${state.currentId}/status`, {
    method: 'POST',
    body: JSON.stringify({ status: 'printed' }),
  });
  await openProject(state.currentId);
};

$('print-btn').onclick = async () => {
  if (state.currentId === null || state.currentVersion === null) return;
  const model = state.models.find((m) => m.version === state.currentVersion);
  const name = model?.title ? `「${model.title}」v${state.currentVersion}` : `v${state.currentVersion}`;
  if (!confirm(`${name} をスライスして P2S で印刷を開始します。\nプリンタの準備（フィラメント・ベッド）は大丈夫ですか？`)) return;
  setLoading(true, 'スライスしてプリンタへ送信しています…（数分かかることがあります）');
  try {
    await api(`/api/projects/${state.currentId}/models/${state.currentVersion}/print`, { method: 'POST' });
    alert('印刷を開始しました！🎉');
    await openProject(state.currentId);
  } catch (e) {
    alert(`印刷を開始できませんでした:\n${e.message}`);
  } finally {
    setLoading(false);
  }
};

// ---------- モデルプレビュー ----------

function setModels(models) {
  state.models = models;
  const panel = $('preview-panel');
  if (models.length === 0) {
    panel.classList.add('hidden');
    return;
  }
  panel.classList.remove('hidden');
  const tabs = $('version-tabs');
  tabs.innerHTML = '';
  for (const m of models) {
    const btn = document.createElement('button');
    btn.textContent = `v${m.version}`;
    if (m.render_error) { btn.classList.add('failed'); btn.title = 'STL変換に失敗'; }
    btn.onclick = () => selectVersion(m.version);
    tabs.appendChild(btn);
  }
  selectVersion(models[models.length - 1].version);
}

function selectVersion(version) {
  state.currentVersion = version;
  const model = state.models.find((m) => m.version === version);
  for (const [i, btn] of [...$('version-tabs').children].entries()) {
    btn.classList.toggle('active', state.models[i].version === version);
  }
  $('model-summary').textContent = model.render_error
    ? `⚠️ STL変換エラー: ${model.render_error}`
    : (model.summary ?? '');
  const stlUrl = `/api/projects/${state.currentId}/models/${version}/stl`;
  $('download-stl').href = stlUrl;
  $('download-scad').href = `/api/projects/${state.currentId}/models/${version}/scad`;
  $('download-stl').style.display = model.stl_path ? '' : 'none';
  updatePrintButton();
  if (model.stl_path) loadSTL(stlUrl);
  else clearViewer();
}

function updatePrintButton() {
  const model = state.models.find((m) => m.version === state.currentVersion);
  const printable = !!(state.canPrint && model?.stl_path);
  $('print-btn').classList.toggle('hidden', !printable);
  $('print-hint').textContent = state.canPrint
    ? 'ボタンひとつでスライス→P2Sへ送信→印刷開始まで行います。'
    : 'STLをBambu Studioで開き、スライスしてP2Sに送信してください。';
}

// ---------- three.js ビューア ----------

let renderer, scene, camera, controls, mesh;

function initViewer() {
  const container = $('viewer');
  renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setPixelRatio(window.devicePixelRatio);
  renderer.setSize(container.clientWidth, container.clientHeight);
  container.appendChild(renderer.domElement);

  scene = new THREE.Scene();
  scene.background = new THREE.Color(0xeef1f5);

  camera = new THREE.PerspectiveCamera(45, container.clientWidth / container.clientHeight, 0.1, 5000);
  camera.position.set(120, 100, 120);

  controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;

  scene.add(new THREE.HemisphereLight(0xffffff, 0x8899aa, 1.1));
  const dir = new THREE.DirectionalLight(0xffffff, 1.2);
  dir.position.set(100, 200, 150);
  scene.add(dir);

  // P2Sのベッド(256x256)をイメージしたグリッド
  const grid = new THREE.GridHelper(256, 16, 0xbbc3cf, 0xd7dde6);
  scene.add(grid);

  (function animate() {
    requestAnimationFrame(animate);
    controls.update();
    renderer.render(scene, camera);
  })();

  new ResizeObserver(() => {
    if (!container.clientWidth) return;
    camera.aspect = container.clientWidth / container.clientHeight;
    camera.updateProjectionMatrix();
    renderer.setSize(container.clientWidth, container.clientHeight);
  }).observe(container);
}

function clearViewer() {
  if (mesh) { scene.remove(mesh); mesh.geometry.dispose(); mesh = null; }
}

async function loadSTL(url) {
  if (!(await ensureThree())) {
    $('viewer').innerHTML =
      '<p style="padding:16px;font-size:12px;color:#6b7280;">3Dプレビューを読み込めませんでした（three.jsのCDNに接続できません）。STLをダウンロードして Bambu Studio で確認してください。</p>';
    return;
  }
  if (!renderer) initViewer();
  clearViewer();
  const res = await fetch(url);
  if (!res.ok) return;
  const buffer = await res.arrayBuffer();
  const geometry = new STLLoader().parse(buffer);
  geometry.computeVertexNormals();
  geometry.computeBoundingBox();

  const material = new THREE.MeshStandardMaterial({ color: 0x16a34a, roughness: 0.55, metalness: 0.05 });
  mesh = new THREE.Mesh(geometry, material);

  // OpenSCADはZ-up、three.jsはY-up。Xまわりに-90度回してベッド上に置く。
  mesh.rotation.x = -Math.PI / 2;

  // 底面中心を原点に合わせる
  const bb = geometry.boundingBox;
  const center = new THREE.Vector3();
  bb.getCenter(center);
  geometry.translate(-center.x, -center.y, -bb.min.z);
  scene.add(mesh);

  // モデルサイズに合わせてカメラ位置を調整
  const size = new THREE.Vector3();
  bb.getSize(size);
  const radius = Math.max(size.x, size.y, size.z);
  camera.position.set(radius * 1.6, radius * 1.4, radius * 1.6);
  controls.target.set(0, size.z / 2, 0);
}

// ---------- プリンタ状態 ----------

async function refreshPrinterStatus() {
  try {
    const s = await api('/api/printer/status');
    const canPrintChanged = state.canPrint !== !!s.can_print;
    state.canPrint = !!s.can_print;
    if (canPrintChanged && state.currentVersion !== null) updatePrintButton();
    const el = $('printer-status');
    const homeEl = $('home-printer-status');
    homeEl.classList.remove('connected');
    if (!s.configured) {
      el.textContent = '🖨 プリンタ: 未設定';
      el.title = s.message ?? '';
      homeEl.textContent = '未接続';
      homeEl.title = s.message ?? '';
    } else if (!s.connected) {
      el.textContent = '🖨 プリンタ: 接続できません';
      el.title = s.message ?? '';
      homeEl.textContent = '接続できません';
      homeEl.title = s.message ?? '';
    } else {
      const progress = s.progress_percent != null ? ` ${s.progress_percent}%` : '';
      el.textContent = `🖨 P2S: ${s.state}${progress} (ノズル${s.nozzle_temperature}° / ベッド${s.bed_temperature}°)`;
      homeEl.textContent = `${s.state}${progress}`;
      homeEl.title = `ノズル ${s.nozzle_temperature}° / ベッド ${s.bed_temperature}°`;
      homeEl.classList.add('connected');
    }
  } catch {
    $('home-printer-status').textContent = '状態を取得できません';
  }
}

// ---------- 初期化 ----------

$('app-version').textContent = `v${APP_VERSION}`;

if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('/sw.js').catch(() => {});
}

refreshProjects();
refreshPrinterStatus();
setInterval(refreshPrinterStatus, 30000);
