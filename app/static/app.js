// three.js（同梱）はプレビュー表示時に遅延読み込みする。
// 万一読み込めなくてもチャットとダウンロードは動作させる。
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
  $('welcome').classList.remove('hidden');
  $('chat').classList.add('hidden');
  $('preview-panel').classList.add('hidden');
  $('welcome-input').value = '';
  refreshProjects();
}

function showChat(project) {
  $('welcome').classList.add('hidden');
  $('chat').classList.remove('hidden');
  $('chat-title').textContent = project.title;
  const chip = $('chat-status');
  chip.className = `status-chip status-${project.status}`;
  chip.textContent = STATUS_LABELS[project.status] ?? project.status;
}

// ---------- チャット ----------

function renderMessages(messages) {
  const box = $('messages');
  box.innerHTML = '';
  for (const m of messages) appendMessage(m.role, m.content);
  box.scrollTop = box.scrollHeight;
}

function appendMessage(role, content) {
  const box = $('messages');
  const div = document.createElement('div');
  div.className = `msg ${role}`;
  div.textContent = content;
  box.appendChild(div);
  box.scrollTop = box.scrollHeight;
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
    if (!s.configured) {
      el.textContent = '🖨 プリンタ: 未設定';
      el.title = s.message ?? '';
    } else if (!s.connected) {
      el.textContent = '🖨 プリンタ: 接続できません';
      el.title = s.message ?? '';
    } else {
      const progress = s.progress_percent != null ? ` ${s.progress_percent}%` : '';
      el.textContent = `🖨 P2S: ${s.state}${progress} (ノズル${s.nozzle_temperature}° / ベッド${s.bed_temperature}°)`;
    }
  } catch { /* プリンタ状態は補助情報のため失敗しても無視 */ }
}

// ---------- 初期化 ----------

if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('/sw.js').catch(() => {});
}

refreshProjects();
refreshPrinterStatus();
setInterval(refreshPrinterStatus, 30000);
