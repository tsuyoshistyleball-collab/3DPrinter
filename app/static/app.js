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

const MODEL_LABELS = {
  haiku: 'Haiku（最軽量・最速）',
  sonnet: 'Sonnet（軽量・バランス型）',
  opus: 'Opus（最高性能）',
};

const BACKEND_NOTES = {
  'claude-code': 'Claudeサブスクリプションの利用枠で動作中です（追加課金なし）。',
  api: 'APIキーによる従量課金で動作中です。',
  mock: 'モック（AIなし）モードで動作中のため、この設定は使われません。',
};

const state = {
  projects: [],
  currentId: null,
  models: [],
  currentVersion: null,
  canPrint: false,
  settings: null,
  backend: null,
  modelOptions: ['haiku', 'sonnet', 'opus'],
  activeJobs: [],
  draftImages: [],
  pendingImages: [],   // トップ画面で選んだ、まだ送っていない写真
  showAllProjects: false,
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
  if (data.active_jobs) {
    state.activeJobs = data.active_jobs;
    syncJobPolling();
  }
  renderProjectList();
}

function isBusy(projectId) {
  return state.activeJobs.some((j) => j.project_id === projectId);
}

function renderProjectList() {
  const ul = $('project-list');
  ul.innerHTML = '';
  for (const p of state.projects) {
    const li = document.createElement('li');
    li.className = p.id === state.currentId ? 'active' : '';
    const title = document.createElement('span');
    title.className = 'title';
    title.textContent = p.title;
    const chip = document.createElement('span');
    // AIが処理中のプロジェクトは、その旨を状態の代わりに出す
    if (isBusy(p.id)) {
      chip.className = 'status-chip status-busy';
      chip.textContent = '⏳ 処理中';
    } else {
      chip.className = `status-chip status-${p.status}`;
      chip.textContent = STATUS_LABELS[p.status] ?? p.status;
    }
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
    return;
  }

  toggle.classList.toggle('hidden', state.projects.length <= 3);
  toggle.firstChild.textContent = state.showAllProjects ? '閉じる ' : 'すべて見る ';
  const projects = state.showAllProjects ? state.projects : state.projects.slice(0, 3);

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
}

async function openProject(id) {
  // 別のプロジェクトを開いたときは会話の先頭から、同じプロジェクトの読み直し
  // （AIの応答が届いた）ときは新しい応答の頭に位置を合わせる
  const switched = state.currentId !== id;
  state.currentId = id;
  const data = await api(`/api/projects/${id}`);
  showChat(data.project);
  renderMessages(data.messages, switched ? 'top' : 'latest');
  setModels(data.models);
  state.draftImages = data.draft_images ?? [];
  renderDraftImages();
  updateChatPending();
  refreshProjects();
}

function showWelcome() {
  state.currentId = null;
  document.body.classList.add('home-mode');
  $('welcome').classList.remove('hidden');
  $('chat').classList.add('hidden');
  $('preview-panel').classList.add('hidden');
  $('welcome-input').value = '';
  for (const image of state.pendingImages) URL.revokeObjectURL(image.previewUrl);
  state.pendingImages = [];
  renderWelcomeImages();
  refreshProjects();
}

function showChat(project) {
  document.body.classList.remove('home-mode');
  $('welcome').classList.add('hidden');
  $('chat').classList.remove('hidden');
  $('chat-title').textContent = project.title;
  const chip = $('chat-status');
  chip.className = `status-chip status-${project.status}`;
  chip.textContent = STATUS_LABELS[project.status] ?? project.status;
}

// ---------- チャット ----------

// **強調** だけ太字にする。HTMLは解釈せずテキストとして扱う。
function renderRich(el, text) {
  el.textContent = '';
  const parts = String(text).split(/\*\*([\s\S]+?)\*\*/g);
  parts.forEach((part, i) => {
    if (!part) return;
    if (i % 2 === 1) {
      const strong = document.createElement('strong');
      strong.textContent = part;
      el.appendChild(strong);
    } else {
      el.appendChild(document.createTextNode(part));
    }
  });
}

// scrollTo: 'top' = 会話の先頭から読ませる / 'latest' = 最後のAI応答の頭に合わせる
function renderMessages(messages, scrollTo = 'top') {
  const box = $('messages');
  box.innerHTML = '';
  let lastReply = null;
  messages.forEach((m, i) => {
    if (m.content) {
      const el = appendMessage(m.role, m.content);
      if (m.role === 'assistant') lastReply = el;
    }
    if (m.images?.length) box.appendChild(renderSentImages(m.images));
    if (m.role !== 'assistant' || !m.questions?.length) return;
    // 最新の質問だけタップして答えられるようにし、過去の質問は読み物として残す
    const isLatest = i === messages.length - 1;
    box.appendChild(isLatest ? buildQuestionForm(m.questions) : buildAnsweredQuestions(m.questions));
  });

  if (scrollTo === 'latest' && lastReply) {
    // 新しい応答は途中からではなく頭から読めるよう、その位置に合わせる。
    // ただし1画面目に収まっているなら動かさない（短い会話で自分の発言を隠さない）。
    const replyTop = lastReply.offsetTop - box.offsetTop;
    box.scrollTop = replyTop < box.clientHeight ? 0 : replyTop;
  } else {
    box.scrollTop = 0;
  }
}

function appendMessage(role, content, { scroll = false } = {}) {
  const box = $('messages');
  const div = document.createElement('div');
  div.className = `msg ${role}`;
  renderRich(div, content);
  box.appendChild(div);
  // 一覧の描画中は最後にまとめて位置を決めるので、ここでは動かさない
  if (scroll) box.scrollTop = box.scrollHeight;
  return div;
}

// ---------- 参考画像 ----------

// Claudeが扱いやすい大きさに落としてから送る。スマホの写真をそのまま上げると
// 回線もトークンも無駄になるため、長辺1568pxのJPEGに変換する。
const MAX_IMAGE_EDGE = 1568;

function shrinkImage(file) {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => {
      URL.revokeObjectURL(url);
      const scale = Math.min(1, MAX_IMAGE_EDGE / Math.max(img.width, img.height));
      const canvas = document.createElement('canvas');
      canvas.width = Math.round(img.width * scale);
      canvas.height = Math.round(img.height * scale);
      canvas.getContext('2d').drawImage(img, 0, 0, canvas.width, canvas.height);
      canvas.toBlob((blob) => (blob ? resolve(blob) : reject(new Error('画像を変換できませんでした'))), 'image/jpeg', 0.85);
    };
    img.onerror = () => {
      URL.revokeObjectURL(url);
      reject(new Error('画像を読み込めませんでした'));
    };
    img.src = url;
  });
}

async function uploadImages(files) {
  if (state.currentId === null) return;
  for (const file of files) {
    try {
      const blob = await shrinkImage(file);
      const form = new FormData();
      form.append('file', blob, 'photo.jpg');
      const res = await fetch(`/api/projects/${state.currentId}/images`, { method: 'POST', body: form });
      if (!res.ok) throw new Error((await res.json()).detail ?? 'アップロードに失敗しました');
      state.draftImages.push(await res.json());
      renderDraftImages();
    } catch (e) {
      alert(`写真を追加できませんでした:\n${e.message}`);
    }
  }
}

function renderDraftImages() {
  const box = $('draft-images');
  box.innerHTML = '';
  box.classList.toggle('hidden', state.draftImages.length === 0);
  for (const image of state.draftImages) {
    const card = document.createElement('div');
    card.className = 'draft-image';

    const thumb = document.createElement('img');
    thumb.src = image.url;
    thumb.alt = '';
    card.appendChild(thumb);

    const note = document.createElement('input');
    note.type = 'text';
    note.className = 'draft-note';
    note.placeholder = 'この写真の説明（例: ここに掛けたい）';
    note.value = image.note ?? '';
    // 説明はその場でサーバーへ保存する（送信ボタンを押す前に消えないように）
    note.onchange = () => {
      image.note = note.value;
      api(`/api/attachments/${image.id}/note`, {
        method: 'POST',
        body: JSON.stringify({ note: note.value }),
      }).catch(() => {});
    };
    card.appendChild(note);

    const remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'draft-remove';
    remove.textContent = '✕';
    remove.title = 'この写真を外す';
    remove.onclick = async () => {
      try {
        await api(`/api/attachments/${image.id}`, { method: 'DELETE' });
      } catch { /* 既に消えていても気にしない */ }
      state.draftImages = state.draftImages.filter((i) => i.id !== image.id);
      renderDraftImages();
    };
    card.appendChild(remove);

    box.appendChild(card);
  }
}

function renderSentImages(images) {
  const row = document.createElement('div');
  row.className = 'sent-images';
  for (const image of images) {
    const figure = document.createElement('figure');
    const img = document.createElement('img');
    img.src = image.url;
    img.alt = image.note ?? '';
    img.onclick = () => window.open(image.url, '_blank');
    figure.appendChild(img);
    if (image.note) {
      const caption = document.createElement('figcaption');
      caption.textContent = image.note;
      figure.appendChild(caption);
    }
    row.appendChild(figure);
  }
  return row;
}

$('add-image-btn').onclick = () => $('image-input').click();
$('image-input').onchange = async (e) => {
  const files = [...e.target.files];
  e.target.value = '';   // 同じ写真をもう一度選べるようにする
  await uploadImages(files);
};

// --- トップ画面の写真 ---
// ここではまだプロジェクトが無いので、送信するまでブラウザ内に保持しておく。
// （相談をやめた場合にサーバーへ孤児ファイルを残さないため）

function renderWelcomeImages() {
  const box = $('welcome-images');
  box.innerHTML = '';
  box.classList.toggle('hidden', state.pendingImages.length === 0);
  state.pendingImages.forEach((image, index) => {
    const card = document.createElement('div');
    card.className = 'draft-image';

    const thumb = document.createElement('img');
    thumb.src = image.previewUrl;
    thumb.alt = '';
    card.appendChild(thumb);

    const note = document.createElement('input');
    note.type = 'text';
    note.className = 'draft-note';
    note.placeholder = 'この写真の説明（例: ここに掛けたい）';
    note.value = image.note;
    note.oninput = () => { image.note = note.value; };
    card.appendChild(note);

    const remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'draft-remove';
    remove.textContent = '✕';
    remove.title = 'この写真を外す';
    remove.onclick = () => {
      URL.revokeObjectURL(image.previewUrl);
      state.pendingImages.splice(index, 1);
      renderWelcomeImages();
    };
    card.appendChild(remove);

    box.appendChild(card);
  });
}

async function uploadPendingImages(projectId) {
  for (const image of state.pendingImages) {
    try {
      const blob = await shrinkImage(image.file);
      const form = new FormData();
      form.append('file', blob, 'photo.jpg');
      form.append('note', image.note ?? '');
      await fetch(`/api/projects/${projectId}/images`, { method: 'POST', body: form });
    } catch { /* 1枚失敗しても相談自体は続ける */ }
    URL.revokeObjectURL(image.previewUrl);
  }
  state.pendingImages = [];
  renderWelcomeImages();
}

$('welcome-image-btn').onclick = () => $('welcome-image-input').click();
$('welcome-image-input').onchange = (e) => {
  for (const file of e.target.files) {
    state.pendingImages.push({ file, note: '', previewUrl: URL.createObjectURL(file) });
  }
  e.target.value = '';
  renderWelcomeImages();
};

// ---------- 質問への回答フォーム ----------

function buildQuestionForm(questions) {
  const card = document.createElement('div');
  card.className = 'q-form';
  const fields = [];

  questions.forEach((q, i) => {
    const item = document.createElement('div');
    item.className = 'q-item';

    const label = document.createElement('div');
    label.className = 'q-text';
    renderRich(label, `${i + 1}. ${q.text}`);
    item.appendChild(label);

    const input = document.createElement('input');
    input.type = 'text';
    input.className = 'q-input';
    input.placeholder = '選択肢をタップ、または自由に入力';

    const chips = document.createElement('div');
    chips.className = 'q-choices';
    for (const choice of q.choices ?? []) {
      const chip = document.createElement('button');
      chip.type = 'button';
      chip.className = 'chip';
      chip.textContent = choice;
      // 選択済みのものをもう一度押したら解除する
      chip.onclick = () => {
        const wasActive = chip.classList.contains('active');
        input.value = wasActive ? '' : choice;
        syncChips();
      };
      chips.appendChild(chip);
    }
    if (chips.children.length) item.appendChild(chips);

    // 手入力すると、一致しなくなった選択肢のハイライトを外す
    const syncChips = () => {
      for (const chip of chips.children) {
        chip.classList.toggle('active', chip.textContent === input.value);
      }
    };
    input.oninput = syncChips;
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.isComposing) {
        e.preventDefault();
        submitAnswers(fields);
      }
    });

    item.appendChild(input);
    card.appendChild(item);
    fields.push({ question: q, input });
  });

  const send = document.createElement('button');
  send.type = 'button';
  send.className = 'btn q-send';
  send.textContent = 'この内容で回答する';
  send.onclick = () => submitAnswers(fields);
  card.appendChild(send);

  const hint = document.createElement('p');
  hint.className = 'hint q-hint';
  hint.textContent = '答えられる項目だけでかまいません。下の入力欄に自由に書いて送ることもできます。';
  card.appendChild(hint);

  return card;
}

function buildAnsweredQuestions(questions) {
  const box = document.createElement('div');
  box.className = 'q-answered';
  questions.forEach((q, i) => {
    const line = document.createElement('div');
    line.textContent = `${i + 1}. ${q.text}`;
    box.appendChild(line);
  });
  return box;
}

function submitAnswers(fields) {
  const lines = fields
    .filter(({ input }) => input.value.trim())
    .map(({ question, input }) => `- ${question.text} → ${input.value.trim()}`);
  const extra = $('chat-input').value.trim();
  if (!lines.length && !extra) {
    alert('1つ以上の項目を選ぶか入力してください。');
    return;
  }
  if (extra) lines.push(extra);
  sendChat(lines.join('\n'));
}

function setLoading(on, text = '処理しています…') {
  $('loading-text').textContent = text;
  $('loading-overlay').classList.toggle('hidden', !on);
}

$('welcome-form').onsubmit = async (e) => {
  e.preventDefault();
  const message = $('welcome-input').value.trim();
  if (!message) return;
  const btn = $('welcome-form').querySelector('.idea-submit');
  btn.disabled = true;
  const withImages = state.pendingImages.length > 0;
  try {
    // 写真がある場合は、登録し終えてからAIを動かす（1通目から写真を見せるため）
    const data = await api('/api/projects', {
      method: 'POST',
      body: JSON.stringify({ message, start: !withImages }),
    });
    const projectId = data.project.id;
    let job = data.job;
    if (withImages) {
      await uploadPendingImages(projectId);
      job = (await api(`/api/projects/${projectId}/start`, { method: 'POST' })).job;
    }
    trackJob(job);
    await openProject(projectId);
  } catch (err) {
    alert(err.message);
  } finally {
    btn.disabled = false;
  }
};

async function sendChat(message) {
  // 写真だけを送りたい場合もあるので、下書き画像があればメッセージ空でも通す
  if (state.currentId === null) return;
  if (!message && !state.draftImages.length) return;
  const projectId = state.currentId;
  if (!message) message = '写真を送ります。参考にしてください。';
  appendMessage('user', message, { scroll: true });   // 送った発言は見えるところへ
  $('chat-input').value = '';
  state.draftImages = [];
  renderDraftImages();
  try {
    const data = await api(`/api/projects/${projectId}/messages`, {
      method: 'POST',
      body: JSON.stringify({ message }),
    });
    trackJob(data.job);
    if (state.currentId === projectId) updateChatPending();
    renderProjectList();
  } catch (e) {
    appendMessage('error', e.message, { scroll: true });
  }
}

$('chat-form').onsubmit = (e) => {
  e.preventDefault();
  sendChat($('chat-input').value.trim());
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

document.querySelectorAll('.prompt-chips button').forEach((button) => {
  button.onclick = () => {
    $('welcome-input').value = button.dataset.prompt;
    $('welcome-input').focus();
  };
});

$('all-projects-btn').onclick = () => {
  state.showAllProjects = !state.showAllProjects;
  renderRecentProjects();
};

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

// ---------- 設定 ----------

async function refreshSettings() {
  try {
    const data = await api('/api/settings');
    state.settings = data.settings;
    state.backend = data.backend;
    state.modelOptions = data.model_options ?? state.modelOptions;
  } catch { /* 設定は補助情報のため失敗しても無視 */ }
  renderConfigLabel();
}

function renderConfigLabel() {
  const s = state.settings;
  $('ai-config').textContent = !s
    ? '設定'
    : s.model_mode === 'split'
      ? `質問 ${s.interview_model} / 造形 ${s.modeling_model}`
      : `モデル ${s.modeling_model}`;
}

function fillModelSelect(select, selected) {
  select.innerHTML = '';
  for (const name of state.modelOptions) {
    const opt = document.createElement('option');
    opt.value = name;
    opt.textContent = MODEL_LABELS[name] ?? name;
    opt.selected = name === selected;
    select.appendChild(opt);
  }
}

function selectedMode() {
  return document.querySelector('input[name="model-mode"]:checked')?.value ?? 'split';
}

// singleモードでは「モデリング担当」を唯一のモデルとして扱う
function updateSettingsMode() {
  const split = selectedMode() === 'split';
  $('interview-field').classList.toggle('hidden', !split);
  $('modeling-label').textContent = split ? 'モデリングの担当' : '使用するモデル';
}

function formatTokens(n) {
  const v = Number(n) || 0;
  if (v >= 1000000) return `${(v / 1000000).toFixed(2)}M`;
  if (v >= 1000) return `${(v / 1000).toFixed(1)}k`;
  return String(v);
}

function usageRow(label, u) {
  const row = document.createElement('div');
  row.className = 'usage-row';
  const name = document.createElement('span');
  name.className = 'usage-label';
  name.textContent = label;
  const value = document.createElement('span');
  // 入力はキャッシュ読み込みを含めた実際の送信量、出力は生成量
  const input = (u.input_tokens ?? 0) + (u.cache_read_tokens ?? 0) + (u.cache_write_tokens ?? 0);
  value.textContent = `入力 ${formatTokens(input)} / 出力 ${formatTokens(u.output_tokens)}（${u.calls ?? 0}回）`;
  row.append(name, value);
  return row;
}

async function renderUsage() {
  const box = $('usage-summary');
  box.innerHTML = '';
  let data;
  try {
    data = await api('/api/usage');
  } catch {
    box.textContent = '取得できませんでした。';
    return;
  }
  if (!data.total?.calls) {
    box.textContent = 'まだ記録がありません。';
    return;
  }
  box.appendChild(usageRow('直近24時間', data.last_24h));
  box.appendChild(usageRow('累計', data.total));
  for (const m of data.total.by_model ?? []) {
    const row = usageRow(`　${m.model}`, m);
    row.classList.add('usage-sub');
    box.appendChild(row);
  }
  const note = document.createElement('p');
  note.className = 'hint';
  note.textContent = state.backend === 'claude-code'
    ? 'サブスクリプションの利用枠から消費されます（追加課金なし）。'
    : '従量課金の対象です。';
  box.appendChild(note);
}

function openSettings() {
  const s = state.settings ?? { model_mode: 'split', interview_model: 'sonnet', modeling_model: 'opus' };
  for (const radio of document.querySelectorAll('input[name="model-mode"]')) {
    radio.checked = radio.value === s.model_mode;
  }
  fillModelSelect($('interview-model'), s.interview_model);
  fillModelSelect($('modeling-model'), s.modeling_model);
  $('web-search').checked = s.web_search !== false;
  updateSettingsMode();
  $('settings-note').textContent = BACKEND_NOTES[state.backend] ?? '';
  $('settings-modal').classList.remove('hidden');
  renderUsage();
}

$('settings-btn').onclick = openSettings;
$('settings-cancel').onclick = () => $('settings-modal').classList.add('hidden');
$('settings-modal').onclick = (e) => {
  if (e.target === $('settings-modal')) $('settings-modal').classList.add('hidden');
};
for (const radio of document.querySelectorAll('input[name="model-mode"]')) {
  radio.onchange = updateSettingsMode;
}

$('settings-save').onclick = async () => {
  try {
    const data = await api('/api/settings', {
      method: 'POST',
      body: JSON.stringify({
        model_mode: selectedMode(),
        interview_model: $('interview-model').value,
        modeling_model: $('modeling-model').value,
        web_search: $('web-search').checked,
      }),
    });
    state.settings = data.settings;
    renderConfigLabel();
    $('settings-modal').classList.add('hidden');
  } catch (e) {
    alert(`設定を保存できませんでした:\n${e.message}`);
  }
};

// ---------- ジョブ（AI応答）の進捗監視 ----------

let jobPollTimer = null;

function trackJob(job) {
  if (!job) return;
  if (!state.activeJobs.some((j) => j.id === job.id)) state.activeJobs.push(job);
  syncJobPolling();
}

function syncJobPolling() {
  const needed = state.activeJobs.length > 0;
  if (needed && !jobPollTimer) jobPollTimer = setInterval(refreshJobs, 2000);
  if (!needed && jobPollTimer) {
    clearInterval(jobPollTimer);
    jobPollTimer = null;
  }
}

// 段階の定義。表示順がそのまま処理順になる。
const STAGES = [
  { key: 'interview', label: '内容を整理' },
  { key: 'modeling', label: '3Dモデルを設計' },
  { key: 'rendering', label: 'STLに変換' },
];
// 書き出したあとの表示（文字数が付く）
const STAGE_MESSAGES = {
  queued: '順番待ちです…',
  interview: '内容を整理しています…',
  thinking: 'AIが書いています…',
  modeling: '設計を書き出しています…',
  rendering: 'STLに変換しています…',
};
// まだ1文字も出ていない間の表示。Opusは書き始める前に数分考えることがある。
const THINKING_MESSAGES = {
  interview: 'ご依頼を読んでいます…',
  thinking: 'AIが考えています…',
  modeling: '形と寸法をじっくり考えています…',
};

function formatDuration(seconds) {
  const s = Math.max(0, Math.round(seconds));
  return s < 60 ? `${s}秒` : `${Math.floor(s / 60)}分${String(s % 60).padStart(2, '0')}秒`;
}

// 表示中のプロジェクトが処理待ちなら進捗を出す
function updateChatPending() {
  const job = state.currentId === null
    ? null
    : state.activeJobs.find((j) => j.project_id === state.currentId);
  $('chat-pending').classList.toggle('hidden', !job);
  if (!job) return;

  const stage = job.status === 'queued' ? 'queued' : (job.stage ?? 'thinking');
  const chars = job.progress_chars ?? 0;
  // 書き始める前は考えている時間。数分かかることがあるので、止まって見えないよう文言を分ける
  const label = chars > 0
    ? `${STAGE_MESSAGES[stage] ?? 'AIが考えています…'} ${chars.toLocaleString()}文字`
    : (THINKING_MESSAGES[stage] ?? STAGE_MESSAGES[stage] ?? 'AIが考えています…');
  $('pending-stage').textContent = label;

  // 経過時間と、過去の実績から出した目安
  const startedAt = job.started_at ?? job.created_at;
  const elapsed = startedAt ? (Date.now() - new Date(startedAt).getTime()) / 1000 : 0;
  // モデリング段階まで来ていればモデル生成の実績を、そうでなければ質問往復の実績を使う。
  // まだ実績が無い種類のときはもう一方で代用する（初回でも目安を出せるように）。
  const t = state.typicalSeconds ?? {};
  const modelingStage = stage === 'modeling' || stage === 'rendering';
  const typical = modelingStage ? (t.modeling ?? t.question) : (t.question ?? t.modeling);
  $('pending-elapsed').textContent = typical
    ? `${formatDuration(elapsed)} / 目安 約${formatDuration(typical)}`
    : formatDuration(elapsed);

  // 進捗バーは経過/目安。目安が無い間や超過時は動き続ける不定表示にする
  const fill = $('pending-bar-fill');
  const ratio = typical ? Math.min(elapsed / typical, 1) : 0;
  fill.classList.toggle('indeterminate', !typical || ratio >= 1);
  fill.style.width = typical && ratio < 1 ? `${Math.round(ratio * 100)}%` : '';

  renderStageSteps(stage);
}

function renderStageSteps(current) {
  const box = $('pending-steps');
  const index = STAGES.findIndex((s) => s.key === current);
  box.innerHTML = '';
  for (const [i, step] of STAGES.entries()) {
    const el = document.createElement('span');
    // index が -1（単一モデル運用や順番待ち）のときはどれも進行中にしない
    const done = index >= 0 && i < index;
    el.className = `step${done ? ' done' : ''}${index === i ? ' current' : ''}`;
    el.textContent = `${done ? '✓ ' : ''}${step.label}`;
    box.appendChild(el);
  }
}

// 経過時間を秒単位で動かす（ジョブの巡回は2秒間隔なので別に回す）
setInterval(() => {
  if (!$('chat-pending').classList.contains('hidden')) updateChatPending();
}, 1000);

async function refreshJobs() {
  let data;
  try {
    data = await api('/api/jobs');
  } catch {
    return; // 一時的な通信失敗は次の巡回で拾う
  }
  const jobs = data.jobs ?? [];
  state.typicalSeconds = data.typical_seconds ?? state.typicalSeconds;
  const previous = state.activeJobs;
  state.activeJobs = jobs;

  const stillActive = new Set(jobs.map((j) => j.id));
  const finished = previous.filter((j) => !stillActive.has(j.id));

  renderProjectList();
  updateChatPending();
  syncJobPolling();

  if (!finished.length) return;
  await refreshProjects();
  for (const job of finished) {
    // 失敗していたら理由をチャットに出す（成功時は再読み込みで応答が現れる）
    try {
      const result = await api(`/api/jobs/${job.id}`);
      if (result.status === 'error' && job.project_id === state.currentId) {
        appendMessage('error', result.error ?? 'AIの処理に失敗しました。', { scroll: true });
      }
    } catch { /* ジョブが消えていても致命的ではない */ }
  }
  if (finished.some((j) => j.project_id === state.currentId)) {
    await openProject(state.currentId);
  }
}

// ---------- バージョン表示 ----------

async function refreshVersion() {
  const el = $('version-badge');
  try {
    const v = await api('/api/version');
    const commit = v.commit ? ` · ${v.commit}` : '';
    // 未コミットの変更が乗っているときは * を付けて色を変える
    el.textContent = `v${v.version}${commit}${v.dirty ? '*' : ''}`;
    el.classList.toggle('dirty', !!v.dirty);
    el.title = [
      `バージョン ${v.version}`,
      v.branch ? `ブランチ ${v.branch}` : null,
      v.commit ? `コミット ${v.commit}` : null,
      v.dirty ? `未コミットの変更 ${v.changed_files} ファイル` : 'コミット済みのコードで稼働中',
    ].filter(Boolean).join('\n');
  } catch {
    el.textContent = '';
  }
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

if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('/sw.js').catch(() => {});
}

refreshProjects();
refreshSettings();
refreshVersion();
refreshPrinterStatus();
setInterval(refreshPrinterStatus, 30000);
