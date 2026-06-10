// ── pdf.js worker ──────────────────────────────────────────────────────────
if (typeof pdfjsLib !== 'undefined') {
  pdfjsLib.GlobalWorkerOptions.workerSrc =
    'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js';
}

const API = window.location.origin;
let selectedFiles = [];

// ── Tab switching ──────────────────────────────────────────────────────────
function switchTab(name, btn) {
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
  btn.classList.add('active');
  document.getElementById('tab-' + name).classList.add('active');
  if (name === 'manage') renderManageTab();
}

let _manageTabHTML = null;

function renderManageTab() {
  const tab = document.getElementById('tab-manage');
  if (!tab) return;
  if (_manageTabHTML === null) _manageTabHTML = tab.innerHTML; // save original once
  if (isActiveSpaceProtected()) {
    tab.innerHTML = `<div style="padding:40px 0;text-align:center;color:#666">
      <div style="font-size:1.1rem;color:#e0e0e0;margin-bottom:12px">This is a demo space</div>
      <div style="font-size:0.9rem;margin-bottom:24px;line-height:1.6">
        It's read-only — you can search the pre-loaded documents but can't upload files.<br>
        Create your own private space to index your own documents.
      </div>
      <button onclick="switchTab('setup', document.querySelectorAll('.tab-btn')[2])"
        style="background:#6ee7b7;color:#0d0d0d;border:none;border-radius:8px;padding:10px 24px;font-weight:600;cursor:pointer;font-size:0.9rem">
        Create your own space →
      </button>
    </div>`;
  } else {
    tab.innerHTML = _manageTabHTML; // restore original when switching back
  }
}

// ── Get a key tab ─────────────────────────────────────────────────────────
async function doGetKey() {
  const code = document.getElementById('invite-input').value.trim();
  const errEl = document.getElementById('invite-error');
  const reveal = document.getElementById('new-key-reveal');
  errEl.textContent = '';
  reveal.style.display = 'none';
  if (!code) { errEl.textContent = 'Enter the invite code first.'; return; }

  try {
    const r = await fetch(API + '/signup', {
      method: 'POST',
      headers: { 'Authorization': 'Bearer ' + code },
    });
    if (r.status === 403) { errEl.textContent = 'Wrong invite code.'; return; }
    if (!r.ok) { errEl.textContent = 'Server error (' + r.status + ').'; return; }
    const data = await r.json();
    document.getElementById('new-key-value').textContent = data.key;
    reveal.style.display = 'block';
    window._pendingNewKey = data.key;
    window._pendingSpaceName = document.getElementById('space-name-input')?.value.trim() || 'my space';
  } catch (e) {
    errEl.textContent = 'Request failed: ' + e.message;
  }
}

function saveNewKey() {
  if (!window._pendingNewKey) return;
  createSpace(window._pendingSpaceName || 'my space', window._pendingNewKey);
  window._pendingNewKey = null;
  window._pendingSpaceName = null;
  switchTab('search', document.querySelector('.tab-btn'));
}

async function saveExistingKey() {
  const k = document.getElementById('existing-key-input').value.trim();
  const label = document.getElementById('existing-space-name')?.value.trim() || 'my space';
  const errEl = document.getElementById('existing-key-error');
  errEl.textContent = '';
  if (!k) { errEl.textContent = 'Paste your key first.'; return; }

  try {
    const r = await fetch(API + '/validate', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({key: k}),
    });
    if (!r.ok) { errEl.textContent = 'Server error.'; return; }
    const data = await r.json();
    if (!data.valid) { errEl.textContent = 'Key not recognised — was it issued by this server?'; return; }
    createSpace(label, k, data.protected);
    switchTab('search', document.querySelector('.tab-btn'));
  } catch (e) {
    errEl.textContent = 'Could not reach server: ' + e.message;
  }
}

// ── Space management ───────────────────────────────────────────────────────

function getSpaces() {
  try { return JSON.parse(localStorage.getItem('ss_spaces') || '[]'); } catch { return []; }
}
function saveSpaces(s) { localStorage.setItem('ss_spaces', JSON.stringify(s)); }

function getActiveKey() {
  const active = localStorage.getItem('ss_active');
  const spaces = getSpaces();
  if (active && spaces.find(s => s.key === active)) return active;
  return spaces[0]?.key || null;
}

function getActiveSpace() {
  const key = getActiveKey();
  return getSpaces().find(s => s.key === key) || null;
}

function createSpace(label, key, protected_ = false) {
  const spaces = getSpaces();
  if (!spaces.find(s => s.key === key)) {
    spaces.push({key, label: label || 'space', created: new Date().toISOString().slice(0, 10), protected: protected_});
    saveSpaces(spaces);
  }
  localStorage.setItem('ss_active', key);
  renderSpaceBar();
  renderSpacesList();
}

function isActiveSpaceProtected() {
  const key = getActiveKey();
  return getSpaces().find(s => s.key === key)?.protected || false;
}

function switchSpace(key) {
  localStorage.setItem('ss_active', key);
  renderSpaceBar();
  document.getElementById('search-output').innerHTML = '';
  const fl = document.getElementById('indexed-files-list');
  if (fl) fl.innerHTML = '<p class="status">Click refresh to see what\'s indexed.</p>';
  renderManageTab();
}

function removeSpace(key) {
  const spaces = getSpaces();
  const sp = spaces.find(s => s.key === key);
  if (!confirm('Remove "' + (sp?.label || 'this space') + '" from this browser?\n\nYour indexed data stays on the server — you can re-add this space later with the same key.')) return;
  saveSpaces(spaces.filter(s => s.key !== key));
  if (localStorage.getItem('ss_active') === key) localStorage.removeItem('ss_active');
  renderSpaceBar();
  renderSpacesList();
}

function renderSpaceBar() {
  const spaces = getSpaces();
  const activeKey = getActiveKey();
  const bar = document.getElementById('space-bar');
  const guide = document.getElementById('getting-started');
  if (!bar) return;

  if (!spaces.length) {
    bar.innerHTML = `<span class="space-none">No spaces yet —</span>`
      + `<button class="secondary" onclick="switchTab('setup', document.querySelectorAll('.tab-btn')[2])" style="padding:6px 12px;font-size:0.8rem">Create a space</button>`;
    if (guide) guide.style.display = 'block';
    return;
  }

  if (guide) guide.style.display = 'none';
  const options = spaces.map(s =>
    `<option value="${s.key}" ${s.key === activeKey ? 'selected' : ''}>${escHtml(s.label)}</option>`
  ).join('');
  bar.innerHTML = `<span class="space-label">Space</span>`
    + `<select class="space-select" onchange="switchSpace(this.value)">${options}</select>`
    + `<button class="secondary" onclick="switchTab('setup', document.querySelectorAll('.tab-btn')[2])" style="padding:6px 12px;font-size:0.8rem">+ New</button>`;
}

function renderSpacesList() {
  const el = document.getElementById('spaces-list');
  if (!el) return;
  const spaces = getSpaces();
  const activeKey = getActiveKey();
  if (!spaces.length) { el.innerHTML = '<p class="status">No spaces yet.</p>'; return; }
  el.innerHTML = spaces.map(s => {
    const isActive = s.key === activeKey;
    return `<div style="display:flex;align-items:center;gap:10px;padding:8px 0;border-bottom:1px solid #1a1a1a;font-size:0.9rem">
      <span style="flex:1;color:${isActive ? '#6ee7b7' : '#aaa'}">${escHtml(s.label)}${isActive ? ' ✓' : ''}</span>
      <span style="font-size:0.75rem;color:#444">${s.created || ''}</span>
      ${!isActive ? `<button class="secondary" onclick="switchSpace('${s.key}'); switchTab('search', document.querySelector('.tab-btn'))" style="padding:3px 8px;font-size:0.75rem">Switch</button>` : ''}
      <button class="secondary" onclick="removeSpace('${s.key}')" style="padding:3px 8px;font-size:0.75rem">✕</button>
    </div>`;
  }).join('');
}

// Migrate old ss_key to spaces format
function migrateOldKey() {
  const old = getActiveKey();
  if (old && !localStorage.getItem('ss_spaces')) {
    saveSpaces([{key: old, label: 'my space', created: new Date().toISOString().slice(0, 10)}]);
    localStorage.setItem('ss_active', old);
    localStorage.removeItem('ss_key');
  }
}

// Legacy stubs (used by a few remaining call sites)
function clearKey() { renderSpaceBar(); }

// ── Search ─────────────────────────────────────────────────────────────────
let _lastSources = [];

function renderResults(results) {
  _lastSources = [];
  return results.map(res => {
    const idx = _lastSources.push(res.source) - 1;
    return '<div class="result">'
      + '<div class="result-meta">'
      + '<span class="result-source">' + escHtml(res.source) + '</span>'
      + '<span class="result-meta-right">'
      + '<button class="full-btn" onclick="openFull(' + idx + ')">↗ full</button>'
      + '<span class="result-score">' + res.score + '</span>'
      + '</span>'
      + '</div>'
      + '<div class="result-text">' + escHtml(res.text) + '</div>'
      + '</div>';
  }).join('');
}

// ── Full document view ───────────────────────────────────────────────────────
async function openFull(idx) {
  const source = _lastSources[idx];
  if (source === undefined) return;
  const key = getActiveKey();
  if (!key) return;

  document.getElementById('full-source').textContent = source;
  document.getElementById('full-meta').textContent = '';
  document.getElementById('full-text').textContent = 'Loading full document…';
  document.getElementById('full-modal').classList.add('open');

  try {
    const r = await fetch(API + '/file', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({key, source}),
    });
    if (!r.ok) {
      document.getElementById('full-text').textContent =
        r.status === 404 ? 'Source not found.' : 'Server error (' + r.status + ').';
      return;
    }
    const data = await r.json();
    document.getElementById('full-meta').textContent =
      data.chunks + ' chunk' + (data.chunks === 1 ? '' : 's') + ' reconstructed';
    document.getElementById('full-text').textContent = data.text;
  } catch (e) {
    document.getElementById('full-text').textContent = 'Request failed: ' + e.message;
  }
}

function closeFullModal() {
  document.getElementById('full-modal').classList.remove('open');
}

async function doSearch() {
  const key = getActiveKey();
  const query = document.getElementById('query').value.trim();
  const synthesize = document.getElementById('synthesize').checked;
  const out = document.getElementById('search-output');

  if (!key) { out.innerHTML = '<p class="error">No space selected — create or switch to a space first.</p>'; return; }
  if (!query) return;

  out.innerHTML = '<p class="status">Searching…</p>';

  try {
    // Step 1: get results immediately (no synthesis)
    const r = await fetch(API + '/search', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({key, query, limit: 5, synthesize: false}),
    });
    if (!r.ok) { out.innerHTML = '<p class="error">Server error (' + r.status + ').</p>'; return; }
    const data = await r.json();

    if (!data.results.length) {
      out.innerHTML = '<p class="status">No results — index some files first.</p>';
      return;
    }

    // Render results right away
    out.innerHTML = (synthesize
      ? '<div id="ai-answer-wrap"><div class="answer"><div class="answer-label">AI answer <span id="ai-timer" style="color:#444;font-weight:normal">0s…</span></div><span style="color:#555">Generating…</span></div></div>'
      : '') + renderResults(data.results);

    // Step 2: synthesis in the background if checked
    if (!synthesize) return;

    const start = Date.now();
    const timerEl = () => document.getElementById('ai-timer');
    const tick = setInterval(() => {
      const el = timerEl();
      if (el) el.textContent = Math.floor((Date.now() - start) / 1000) + 's…';
    }, 1000);

    const TIMEOUT = 90000; // 90s — qwen3:32b cold start can take 30-60s to load
    const controller = new AbortController();
    const tId = setTimeout(() => controller.abort(), TIMEOUT);
    let synthDone = false;

    try {
      const sr = await fetch(API + '/search', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({key, query, limit: 5, synthesize: true}),
        signal: controller.signal,
      });
      clearTimeout(tId);
      clearInterval(tick);
      const sd = await sr.json();
      synthDone = true;
      const wrap = document.getElementById('ai-answer-wrap');
      if (wrap) {
        wrap.innerHTML = sd.answer
          ? '<div class="answer"><div class="answer-label">AI answer</div>' + escHtml(sd.answer) + '</div>'
          : '';
      }
    } catch (e) {
      if (synthDone) return; // answer already rendered — ignore late errors
      clearInterval(tick);
      const wrap = document.getElementById('ai-answer-wrap');
      if (wrap) wrap.innerHTML = '<div class="answer" style="border-color:#333"><div class="answer-label" style="color:#666">AI answer</div><span style="color:#555">'
        + (e.name === 'AbortError' ? 'Timed out after 90s — model may still be loading, try again.' : 'Failed: ' + escHtml(e.message))
        + '</span></div>';
    }

  } catch (e) {
    out.innerHTML = '<p class="error">Request failed: ' + escHtml(e.message) + '</p>';
  }
}

// ── URL fetch ─────────────────────────────────────────────────────────────
async function fetchUrl() {
  const key = getActiveKey();
  const url = document.getElementById('url-input')?.value.trim();
  const status = document.getElementById('url-status');
  if (!key) { if (status) status.textContent = 'No space selected.'; return; }
  if (!url) { if (status) status.textContent = 'Paste a URL first.'; return; }
  if (status) status.textContent = 'Fetching…';

  try {
    const fd = new FormData();
    fd.append('key', key);
    fd.append('url', url);
    const r = await fetch(API + '/fetch', {method: 'POST', body: fd});
    if (!r.ok) {
      const d = await r.json().catch(() => ({}));
      if (status) status.style.color = '#f87171';
      if (status) status.textContent = d.detail || 'Fetch failed.';
      return;
    }
    const d = await r.json();
    if (!d.text?.trim()) { if (status) status.textContent = 'Page had no readable text.'; return; }

    // Add as a virtual file entry into the existing file list
    const urlFile = {name: d.title || url, _fetchedText: d.text, _fetchedUrl: url};
    selectedFiles = [...selectedFiles, urlFile];
    renderFileList();
    if (status) { status.style.color = '#6ee7b7'; status.textContent = `Fetched: ${d.title || url}`; }
    document.getElementById('url-input').value = '';
  } catch (e) {
    if (status) { status.style.color = '#f87171'; status.textContent = 'Error: ' + e.message; }
  }
}

// ── File selection ─────────────────────────────────────────────────────────
function onDragOver(e) {
  e.preventDefault();
  document.getElementById('drop-zone').classList.add('dragover');
}
function onDragLeave(e) {
  document.getElementById('drop-zone').classList.remove('dragover');
}
function onDrop(e) {
  e.preventDefault();
  document.getElementById('drop-zone').classList.remove('dragover');
  const files = [];
  for (const item of e.dataTransfer.items) {
    if (item.kind === 'file') files.push(item.getAsFile());
  }
  setSelectedFiles(files);
}
function onFilePick(fileList) {
  setSelectedFiles(Array.from(fileList));
}

const SUPPORTED_EXT = new Set(['.pdf','.docx','.txt','.md','.markdown','.png','.jpg','.jpeg']);

function setSelectedFiles(files) {
  selectedFiles = files.filter(f => {
    const ext = '.' + f.name.split('.').pop().toLowerCase();
    return SUPPORTED_EXT.has(ext);
  });
  renderFileList();
}

function renderFileList() {
  const box   = document.getElementById('file-list-box');
  const items = document.getElementById('file-list-items');
  const btnRow = document.getElementById('index-btn-row');
  const summary = document.getElementById('summary');
  const manErr = document.getElementById('manage-error');

  summary.style.display = 'none';
  manErr.innerHTML = '';

  if (!selectedFiles.length) {
    box.style.display = 'none';
    btnRow.style.display = 'none';
    return;
  }
  box.style.display = 'block';
  btnRow.style.display = 'block';

  const preview = selectedFiles.slice(0, 8);
  const rest    = selectedFiles.length - preview.length;
  let html = preview.map(f =>
    '<div class="file-item">' + escHtml(f._fetchedUrl || f.webkitRelativePath || f.name) + '</div>'
  ).join('');
  if (rest > 0) html += '<div class="file-more">and ' + rest + ' more…</div>';
  items.innerHTML = html;
}

// ── Chunking (mirrors Python) ──────────────────────────────────────────────
function chunkText(text, source) {
  const words = text.trim().split(/\s+/);
  const WINDOW = 500, OVERLAP = 50;
  const chunks = [];
  let i = 0;
  while (i < words.length) {
    const slice = words.slice(i, i + WINDOW).join(' ');
    chunks.push({text: slice, source, chunk_index: chunks.length});
    if (i + WINDOW >= words.length) break;
    i += WINDOW - OVERLAP;
  }
  return chunks;
}

// ── Text extraction ────────────────────────────────────────────────────────
async function extractText(file) {
  // URL-fetched virtual file
  if (file._fetchedText !== undefined) {
    return {text: file._fetchedText, source: file._fetchedUrl || file.name};
  }

  const name = file.name.toLowerCase();
  const ext  = '.' + name.split('.').pop();
  const source = file.webkitRelativePath || file.name;

  if (ext === '.pdf') {
    const buf = await file.arrayBuffer();
    const pdf = await pdfjsLib.getDocument({data: buf}).promise;
    let text = '';
    for (let p = 1; p <= pdf.numPages; p++) {
      const page    = await pdf.getPage(p);
      const content = await page.getTextContent();
      text += content.items.map(i => i.str).join(' ') + '\\n';
    }
    return {text, source};
  }

  if (ext === '.docx') {
    const buf = await file.arrayBuffer();
    const result = await mammoth.extractRawText({arrayBuffer: buf});
    return {text: result.value, source};
  }

  if (ext === '.txt' || ext === '.md' || ext === '.markdown') {
    return new Promise((resolve, reject) => {
      const fr = new FileReader();
      fr.onload  = () => resolve({text: fr.result, source});
      fr.onerror = () => reject(new Error('FileReader error'));
      fr.readAsText(file);
    });
  }

  if (ext === '.png' || ext === '.jpg' || ext === '.jpeg') {
    const key = getActiveKey();
    if (!key) throw new Error('No key saved');
    const fd = new FormData();
    fd.append('key', key);
    fd.append('file', file);
    const r = await fetch(API + '/extract', {method: 'POST', body: fd});
    if (r.status === 403) throw new Error('Invalid key');
    if (!r.ok) throw new Error('OCR failed (' + r.status + ')');
    const data = await r.json();
    return {text: data.text, source};
  }

  throw new Error('Unsupported file type: ' + ext);
}

// ── Indexing ───────────────────────────────────────────────────────────────
async function doIndex() {
  const key = getActiveKey();
  const manErr = document.getElementById('manage-error');
  manErr.innerHTML = '';

  if (!key) {
    manErr.innerHTML = '<p class="inline-error">No key saved — save your key first.</p>';
    return;
  }
  if (!selectedFiles.length) return;

  const btn      = document.getElementById('index-btn');
  const progWrap = document.getElementById('progress-wrap');
  const progBar  = document.getElementById('progress-bar');
  const progLabel = document.getElementById('progress-label');
  const summary  = document.getElementById('summary');

  btn.disabled = true;
  progWrap.style.display = 'block';
  summary.style.display  = 'none';

  let totalChunks = 0;
  let filesOk     = 0;

  try {
    for (let fi = 0; fi < selectedFiles.length; fi++) {
      const file = selectedFiles[fi];
      const pct  = Math.round((fi / selectedFiles.length) * 100);
      progBar.style.width  = pct + '%';
      progLabel.textContent = 'Extracting: ' + (file.webkitRelativePath || file.name);

      let extracted;
      try {
        extracted = await extractText(file);
      } catch (e) {
        console.warn('Skipping', file.name, e.message);
        continue;
      }

      if (!extracted.text.trim()) continue;

      const chunks = chunkText(extracted.text, extracted.source);
      if (!chunks.length) continue;

      // Send in batches of 50
      const BATCH = 50;
      for (let bi = 0; bi < chunks.length; bi += BATCH) {
        const batch = chunks.slice(bi, bi + BATCH);
        progLabel.textContent = 'Indexing: ' + (file.webkitRelativePath || file.name)
          + ' (' + Math.min(bi + BATCH, chunks.length) + '/' + chunks.length + ' chunks)';

        const r = await fetch(API + '/index', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({key, chunks: batch}),
        });
        if (!r.ok) {
          const err = await r.json().catch(() => ({detail: 'Unknown error'}));
          throw new Error('Index failed: ' + (err.detail || r.status));
        }
        const data = await r.json();
        totalChunks += data.indexed;
      }
      filesOk++;
    }

    progBar.style.width   = '100%';
    progLabel.textContent = 'Done';
    summary.style.display = 'block';
    summary.textContent   = totalChunks + ' chunks from ' + filesOk + ' file'
      + (filesOk !== 1 ? 's' : '') + ' indexed';

  } catch (e) {
    manErr.innerHTML = '<p class="inline-error">' + escHtml(e.message) + '</p>';
  } finally {
    btn.disabled = false;
    progWrap.style.display = 'none';
  }
}

// ── Indexed files list ────────────────────────────────────────────────────
async function loadIndexedFiles() {
  const key = getActiveKey();
  const el  = document.getElementById('indexed-files-list');
  if (!key) { el.innerHTML = '<p class="status">Save a key first.</p>'; return; }
  el.innerHTML = '<p class="status">Loading…</p>';
  try {
    const r = await fetch(API + '/list', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({key}),
    });
    if (!r.ok) { el.innerHTML = '<p class="error">Server error.</p>'; return; }
    const d = await r.json();
    if (!d.sources.length) { el.innerHTML = '<p class="status">Nothing indexed yet.</p>'; return; }
    el.innerHTML = d.sources.map(s => {
      const safe = escHtml(s.source);
      return `<div class="file-entry">
        <span class="file-source" title="${safe}">${safe}</span>
        <span class="file-chunks">${s.chunks} chunk${s.chunks !== 1 ? 's' : ''}</span>
        <button class="secondary" onclick="deleteSource('${safe}')" style="padding:3px 8px;font-size:0.75rem;flex-shrink:0">✕ remove</button>
      </div>`;
    }).join('');
  } catch (e) {
    el.innerHTML = '<p class="error">Failed: ' + escHtml(e.message) + '</p>';
  }
}

async function deleteSource(source) {
  const key = getActiveKey();
  if (!key) return;
  if (!confirm('Remove "' + source + '" from the index?')) return;
  const el = document.getElementById('indexed-files-list');
  try {
    const r = await fetch(API + '/index', {
      method: 'DELETE',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({key, source}),
    });
    if (!r.ok) { alert('Delete failed.'); return; }
    const d = await r.json();
    loadIndexedFiles();
  } catch (e) {
    alert('Error: ' + e.message);
  }
}

// ── Reset / clear ──────────────────────────────────────────────────────────
function openResetModal() {
  document.getElementById('confirm-input').value = '';
  document.getElementById('confirm-reset-btn').disabled = true;
  document.getElementById('reset-modal').classList.add('open');
  document.getElementById('confirm-input').focus();
}
function closeResetModal() {
  document.getElementById('reset-modal').classList.remove('open');
}
function onConfirmInput() {
  const val = document.getElementById('confirm-input').value;
  document.getElementById('confirm-reset-btn').disabled = (val !== 'DELETE');
}
async function doReset() {
  const key = getActiveKey();
  if (!key) { closeResetModal(); return; }

  try {
    const r = await fetch(API + '/clear', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({key}),
    });
    if (!r.ok) throw new Error('Server error ' + r.status);
    closeResetModal();
    const summary = document.getElementById('summary');
    summary.style.display = 'block';
    summary.textContent   = 'Index cleared. All chunks deleted.';
    selectedFiles = [];
    renderFileList();
  } catch (e) {
    closeResetModal();
    document.getElementById('manage-error').innerHTML =
      '<p class="inline-error">' + escHtml(e.message) + '</p>';
  }
}

// ── Utilities ──────────────────────────────────────────────────────────────
function escHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/\\n/g, '<br>');
}

// ── Health check ──────────────────────────────────────────────────────────
async function checkHealth() {
  try {
    const r = await fetch(API + '/health');
    if (!r.ok) return;
    const d = await r.json();
    const banner = document.getElementById('health-banner');
    if (banner) banner.style.display = d.embed ? 'none' : 'block';
    const synth = document.getElementById('synthesize');
    const synthLabel = document.getElementById('synthesize-label');
    if (synth) {
      synth.disabled = !d.synthesis;
      if (!d.synthesis) {
        synth.checked = false;
        if (synthLabel) synthLabel.innerHTML =
          'AI answer <span style="color:#444">(offline)</span> '
          + '<button class="secondary" onclick="startAI()" style="padding:2px 8px;font-size:0.75rem;margin-left:6px" title="Starts Ollama and loads qwen3:32b (~60s)">Start AI</button>';
      } else {
        if (synthLabel) synthLabel.textContent = 'AI answer (slower)';
      }
    }
  } catch {}
}

let _aiStartTimer = null;
async function startAI() {
  const synthLabel = document.getElementById('synthesize-label');
  if (synthLabel) synthLabel.innerHTML = 'AI answer — <span id="ai-start-status" style="color:#6ee7b7">starting…</span>';

  try {
    const r = await fetch(API + '/ai/start', {method: 'POST'});
    const d = await r.json();
    if (!d.ok) {
      if (synthLabel) synthLabel.innerHTML = 'AI answer <span style="color:#f87171">(start failed)</span>';
      return;
    }
    const msg = 'loading model (~60s)…';
    if (synthLabel) synthLabel.innerHTML = `AI answer — <span id="ai-start-status" style="color:#6ee7b7">${msg}</span>`;
    // Poll health every 8s until synthesis comes online
    let polls = 0;
    _aiStartTimer = setInterval(async () => {
      polls++;
      try {
        const hr = await fetch(API + '/health');
        const hd = await hr.json();
        if (hd.synthesis) {
          clearInterval(_aiStartTimer);
          checkHealth(); // re-render with enabled checkbox
        } else if (polls > 15) {
          clearInterval(_aiStartTimer);
          if (synthLabel) synthLabel.innerHTML = 'AI answer <span style="color:#444">(timed out — try again)</span>';
        } else {
          const el = document.getElementById('ai-start-status');
          if (el) el.textContent = `loading model (${polls * 8}s…)`;
        }
      } catch {}
    }, 8000);
  } catch (e) {
    if (synthLabel) synthLabel.innerHTML = 'AI answer <span style="color:#f87171">(error)</span>';
  }
}

// ── Boot ───────────────────────────────────────────────────────────────────
migrateOldKey();
renderSpaceBar();
renderSpacesList();
checkHealth();
const q = document.getElementById('query');
if (q) q.focus();
