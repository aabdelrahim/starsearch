"""
Starsearch API
Key-based vector search proxy with at-rest encryption.
Chunk text and source are AES-GCM encrypted in Qdrant payloads;
vectors are generated from plaintext so search still works.

Env vars:
  QDRANT_URL      default http://qdrant:6333
  OLLAMA_URL      default http://host-gateway:11434  (Ollama on Docker host; override as needed)
  EMBED_MODEL     default nomic-embed-text
  LLM_MODEL       default qwen3:32b
  SIGNUP_SECRET   if set, POST /signup requires Authorization: Bearer <secret>
"""

import asyncio
import base64
import hashlib
import io
import os
import secrets
from contextlib import asynccontextmanager

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import FastAPI, Header, HTTPException, UploadFile, File, Form
from fastapi.responses import HTMLResponse
from PIL import Image
import pytesseract
from pydantic import BaseModel
import httpx
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

QDRANT_URL    = os.environ.get("QDRANT_URL",    "http://qdrant:6333")
OLLAMA_URL    = os.environ.get("OLLAMA_URL",    "http://host-gateway:11434")
EMBED_MODEL   = os.environ.get("EMBED_MODEL",   "nomic-embed-text")
LLM_MODEL     = os.environ.get("LLM_MODEL",     "qwen3:32b")
SIGNUP_SECRET = os.environ.get("SIGNUP_SECRET", "")

MAX_CHUNKS     = 500
MAX_CHUNK_LEN  = 10_000
VECTOR_SIZE    = 768  # nomic-embed-text

qdrant = AsyncQdrantClient(url=QDRANT_URL, timeout=30)


@asynccontextmanager
async def lifespan(app: FastAPI):
    for attempt in range(12):
        try:
            await qdrant.get_collections()
            break
        except Exception:
            if attempt == 11:
                raise RuntimeError("Qdrant not reachable after 60s — giving up")
            await asyncio.sleep(5)
    yield


app = FastAPI(title="*search", docs_url=None, redoc_url=None, lifespan=lifespan)


# ─── Encryption helpers ────────────────────────────────────────────────────────

def _enc_key(api_key: str) -> bytes:
    """Derive a 32-byte AES key from the user's API key via SHA-256."""
    return hashlib.sha256(f"starsearch-enc-v1:{api_key}".encode()).digest()


def encrypt_field(api_key: str, plaintext: str) -> str:
    """AES-GCM encrypt a string field; returns base64-encoded nonce+ciphertext."""
    key = _enc_key(api_key)
    aesgcm = AESGCM(key)
    nonce = secrets.token_bytes(12)
    ct = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
    return base64.b64encode(nonce + ct).decode("ascii")


def decrypt_field(api_key: str, encoded: str) -> str:
    """Decrypt a field produced by encrypt_field."""
    key = _enc_key(api_key)
    aesgcm = AESGCM(key)
    raw = base64.b64decode(encoded)
    nonce, ct = raw[:12], raw[12:]
    return aesgcm.decrypt(nonce, ct, None).decode("utf-8")


# ─── Core helpers ──────────────────────────────────────────────────────────────

def key_to_collection(key: str) -> str:
    return "ss_" + hashlib.sha256(key.encode()).hexdigest()[:32]


def stable_point_id(source: str, chunk_index: int) -> int:
    h = hashlib.sha256(f"{source}_{chunk_index}".encode()).hexdigest()
    return int(h, 16) % (2**63)


async def embed(text: str) -> list[float]:
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(
            f"{OLLAMA_URL}/api/embeddings",
            json={"model": EMBED_MODEL, "prompt": text},
        )
        r.raise_for_status()
        return r.json()["embedding"]


async def ensure_collection(name: str):
    existing = {c.name for c in (await qdrant.get_collections()).collections}
    if name not in existing:
        await qdrant.create_collection(
            collection_name=name,
            vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
        )


async def collection_exists(name: str) -> bool:
    existing = {c.name for c in (await qdrant.get_collections()).collections}
    return name in existing


# ─── Models ───────────────────────────────────────────────────────────────────

class Chunk(BaseModel):
    text: str
    source: str
    chunk_index: int

class IndexRequest(BaseModel):
    key: str
    chunks: list[Chunk]

class SearchRequest(BaseModel):
    key: str
    query: str
    limit: int = 5
    synthesize: bool = False

class ClearRequest(BaseModel):
    key: str


# ─── Routes ───────────────────────────────────────────────────────────────────

@app.post("/signup")
async def signup(authorization: str = Header(default="")):
    if SIGNUP_SECRET and authorization != f"Bearer {SIGNUP_SECRET}":
        raise HTTPException(status_code=403, detail="Forbidden")
    key = secrets.token_urlsafe(32)
    await ensure_collection(key_to_collection(key))
    return {
        "key": key,
        "note": "Save this key — it cannot be recovered. Lose it and your index is inaccessible.",
    }


@app.post("/index")
async def index(req: IndexRequest):
    if len(req.chunks) > MAX_CHUNKS:
        raise HTTPException(status_code=400, detail=f"Too many chunks (max {MAX_CHUNKS} per request)")
    for chunk in req.chunks:
        if len(chunk.text) > MAX_CHUNK_LEN:
            raise HTTPException(status_code=400, detail="Chunk text exceeds maximum length")

    collection = key_to_collection(req.key)
    await ensure_collection(collection)

    points = []
    for chunk in req.chunks:
        # Embed plaintext so vector search works
        vector = await embed(chunk.text)
        # Encrypt text and source at rest
        enc_text   = encrypt_field(req.key, chunk.text)
        enc_source = encrypt_field(req.key, chunk.source)
        points.append(PointStruct(
            id=stable_point_id(chunk.source, chunk.chunk_index),
            vector=vector,
            payload={
                "text": enc_text,
                "source": enc_source,
                "chunk_index": chunk.chunk_index,
            },
        ))

    if points:
        await qdrant.upsert(collection_name=collection, points=points)
    return {"indexed": len(points)}


@app.post("/search")
async def search(req: SearchRequest):
    collection = key_to_collection(req.key)
    if not await collection_exists(collection):
        return {"results": [], "answer": None}

    query_vector = await embed(req.query)
    hits = await qdrant.search(collection_name=collection, query_vector=query_vector, limit=req.limit)

    results = []
    for h in hits:
        try:
            text   = decrypt_field(req.key, h.payload["text"])
            source = decrypt_field(req.key, h.payload["source"])
        except Exception:
            # Malformed or unencrypted legacy payload — skip gracefully
            continue
        results.append({"text": text, "source": source, "score": round(h.score, 3)})

    answer = None
    if req.synthesize and results:
        context = "\n\n".join(f"[{r['source']}]\n{r['text']}" for r in results)
        async with httpx.AsyncClient(timeout=120) as client:
            r = await client.post(
                f"{OLLAMA_URL}/api/chat",
                json={
                    "model": LLM_MODEL,
                    "stream": False,
                    "messages": [
                        {"role": "system", "content": "Answer using only the provided document excerpts. Be concise."},
                        {"role": "user", "content": f"Documents:\n{context}\n\nQuestion: {req.query}"},
                    ],
                },
            )
            r.raise_for_status()
            answer = r.json()["message"]["content"]

    return {"results": results, "answer": answer}


@app.post("/extract")
async def extract(key: str = Form(...), file: UploadFile = File(...)):
    """Accept an image upload, validate key, run OCR, return extracted text."""
    collection = key_to_collection(key)
    if not await collection_exists(collection):
        raise HTTPException(status_code=403, detail="Invalid key")

    try:
        data = await file.read()
        image = Image.open(io.BytesIO(data))
        text = pytesseract.image_to_string(image)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"OCR failed: {str(e)}")

    return {"text": text}


@app.post("/clear")
async def clear(req: ClearRequest):
    """Delete the user's Qdrant collection if it exists."""
    collection = key_to_collection(req.key)
    if await collection_exists(collection):
        await qdrant.delete_collection(collection_name=collection)
    return {"cleared": True}


# ─── UI ───────────────────────────────────────────────────────────────────────

HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>*search</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/mammoth/1.7.0/mammoth.browser.min.js"></script>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: #0d0d0d; color: #e0e0e0; font-family: system-ui, sans-serif; min-height: 100vh; }
  .wrap { max-width: 700px; margin: 0 auto; padding: 48px 24px; }
  h1 { font-size: 2rem; font-weight: 700; letter-spacing: -0.5px; margin-bottom: 4px; }
  h1 span { color: #6ee7b7; }
  .sub { color: #666; font-size: 0.85rem; margin-bottom: 28px; }

  /* Key bar */
  .key-bar { display: flex; gap: 8px; margin-bottom: 20px; }
  .key-bar input { flex: 1; background: #1a1a1a; border: 1px solid #333; border-radius: 8px;
    color: #e0e0e0; padding: 10px 14px; font-size: 0.85rem; font-family: monospace; }
  .key-bar input:focus { outline: none; border-color: #6ee7b7; }
  .key-saved { color: #6ee7b7; font-size: 0.85rem; align-self: center; }

  /* Tab nav */
  .tabs { display: flex; gap: 0; margin-bottom: 28px; border-bottom: 1px solid #252525; }
  .tab-btn { background: none; border: none; border-radius: 0; color: #666; font-size: 0.9rem;
    font-weight: 500; padding: 10px 20px; cursor: pointer; border-bottom: 2px solid transparent;
    margin-bottom: -1px; transition: color 0.15s; }
  .tab-btn:hover { color: #e0e0e0; background: none; }
  .tab-btn.active { color: #6ee7b7; border-bottom-color: #6ee7b7; }
  .tab-panel { display: none; }
  .tab-panel.active { display: block; }

  /* Shared buttons */
  button { background: #6ee7b7; color: #0d0d0d; border: none; border-radius: 8px;
    padding: 10px 20px; font-weight: 600; cursor: pointer; font-size: 0.9rem; white-space: nowrap; }
  button:hover { background: #86efca; }
  button:disabled { background: #2a4a3a; color: #4a7a5a; cursor: not-allowed; }
  button.secondary { background: #1a1a1a; color: #aaa; border: 1px solid #333; }
  button.secondary:hover { border-color: #555; color: #e0e0e0; background: #1a1a1a; }
  button.danger { background: #7f1d1d; color: #fca5a5; border: 1px solid #991b1b; }
  button.danger:hover { background: #991b1b; }

  /* Search tab */
  .search-bar { display: flex; gap: 8px; margin-bottom: 16px; }
  .search-bar input { flex: 1; background: #1a1a1a; border: 1px solid #333; border-radius: 8px;
    color: #e0e0e0; padding: 12px 16px; font-size: 1rem; }
  .search-bar input:focus { outline: none; border-color: #6ee7b7; }
  .options { display: flex; align-items: center; gap: 10px; margin-bottom: 28px; color: #666; font-size: 0.85rem; }
  .options label { display: flex; align-items: center; gap: 6px; cursor: pointer; }
  .options input[type=checkbox] { accent-color: #6ee7b7; }

  .answer { background: #141f1a; border: 1px solid #2a4a3a; border-radius: 10px;
    padding: 18px 20px; margin-bottom: 24px; font-size: 0.95rem; line-height: 1.6; color: #d0ead8; }
  .answer-label { font-size: 0.75rem; color: #6ee7b7; text-transform: uppercase;
    letter-spacing: 1px; margin-bottom: 10px; }
  .result { background: #141414; border: 1px solid #252525; border-radius: 10px;
    padding: 16px 18px; margin-bottom: 12px; }
  .result-meta { display: flex; justify-content: space-between; align-items: center;
    margin-bottom: 8px; }
  .result-source { font-size: 0.8rem; color: #6ee7b7; font-weight: 500; }
  .result-score { font-size: 0.75rem; color: #444; }
  .result-text { font-size: 0.9rem; color: #bbb; line-height: 1.55; }

  /* Manage tab */
  .drop-zone { border: 2px dashed #333; border-radius: 12px; padding: 40px 24px;
    text-align: center; color: #555; transition: border-color 0.2s, background 0.2s;
    margin-bottom: 16px; cursor: pointer; }
  .drop-zone.dragover { border-color: #6ee7b7; background: #0f1f18; color: #6ee7b7; }
  .drop-zone p { margin-bottom: 12px; font-size: 0.95rem; }
  .drop-zone .drop-hint { font-size: 0.8rem; color: #444; margin-top: 8px; margin-bottom: 0; }
  .pick-btns { display: flex; gap: 8px; justify-content: center; }

  .file-list { background: #141414; border: 1px solid #252525; border-radius: 10px;
    padding: 14px 16px; margin-bottom: 16px; }
  .file-list-title { font-size: 0.8rem; color: #666; margin-bottom: 8px; text-transform: uppercase; letter-spacing: 0.5px; }
  .file-item { font-size: 0.85rem; color: #aaa; padding: 3px 0; white-space: nowrap;
    overflow: hidden; text-overflow: ellipsis; }
  .file-more { font-size: 0.8rem; color: #555; margin-top: 4px; }

  .index-btn-row { margin-bottom: 20px; }

  /* Progress */
  .progress-wrap { margin-bottom: 16px; display: none; }
  .progress-label { font-size: 0.8rem; color: #6ee7b7; margin-bottom: 6px;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .progress-bar-bg { background: #1a1a1a; border-radius: 4px; height: 6px; overflow: hidden; }
  .progress-bar-fill { background: #6ee7b7; height: 6px; width: 0%; transition: width 0.3s; border-radius: 4px; }

  .summary { background: #141f1a; border: 1px solid #2a4a3a; border-radius: 10px;
    padding: 14px 18px; margin-bottom: 20px; font-size: 0.9rem; color: #6ee7b7;
    display: none; }

  /* Danger zone */
  .danger-zone { border: 1px solid #7f1d1d; border-radius: 10px; padding: 18px 20px; margin-top: 32px; }
  .danger-title { font-size: 0.75rem; color: #f87171; text-transform: uppercase;
    letter-spacing: 1px; margin-bottom: 10px; }
  .danger-desc { font-size: 0.85rem; color: #666; margin-bottom: 14px; }

  /* Modal */
  .modal-overlay { position: fixed; inset: 0; background: rgba(0,0,0,0.75);
    display: flex; align-items: center; justify-content: center; z-index: 100; display: none; }
  .modal-overlay.open { display: flex; }
  .modal { background: #1a1a1a; border: 1px solid #333; border-radius: 12px;
    padding: 28px 24px; width: 100%; max-width: 380px; }
  .modal h3 { font-size: 1rem; margin-bottom: 10px; color: #f87171; }
  .modal p { font-size: 0.85rem; color: #888; margin-bottom: 16px; line-height: 1.5; }
  .modal input { width: 100%; background: #0d0d0d; border: 1px solid #333; border-radius: 8px;
    color: #e0e0e0; padding: 10px 14px; font-size: 0.9rem; margin-bottom: 16px; }
  .modal input:focus { outline: none; border-color: #f87171; }
  .modal-btns { display: flex; gap: 8px; justify-content: flex-end; }

  /* Shared status/error */
  .status { color: #555; font-size: 0.85rem; text-align: center; padding: 24px 0; }
  .error { color: #f87171; font-size: 0.85rem; text-align: center; padding: 12px 0; }
  .inline-error { color: #f87171; font-size: 0.85rem; margin-top: 8px; }

  /* Key setup tab */
  .key-section { margin-bottom: 32px; }
  .key-section h3 { font-size: 0.85rem; color: #6ee7b7; text-transform: uppercase;
    letter-spacing: 1px; margin-bottom: 10px; }
  .key-section p { font-size: 0.85rem; color: #666; margin-bottom: 14px; line-height: 1.55; }
  .key-row { display: flex; gap: 8px; }
  .key-row input { flex: 1; background: #1a1a1a; border: 1px solid #333; border-radius: 8px;
    color: #e0e0e0; padding: 10px 14px; font-size: 0.85rem; }
  .key-row input:focus { outline: none; border-color: #6ee7b7; }
  .key-reveal { background: #0d0d0d; border: 1px solid #2a4a3a; border-radius: 8px;
    padding: 14px 16px; margin-top: 14px; display: none; }
  .key-reveal-label { font-size: 0.75rem; color: #6ee7b7; text-transform: uppercase;
    letter-spacing: 1px; margin-bottom: 8px; }
  .key-reveal-value { font-family: monospace; font-size: 0.8rem; color: #a7f3d0;
    word-break: break-all; margin-bottom: 12px; }
  .key-warning { font-size: 0.8rem; color: #f59e0b; line-height: 1.5; margin-bottom: 12px; }
  .divider { border: none; border-top: 1px solid #1f1f1f; margin: 28px 0; }
</style>
</head>
<body>
<div class="wrap">
  <h1><span>*</span>search</h1>
  <p class="sub">Search your documents — powered by your GPU</p>

  <!-- Shared key bar -->
  <div class="key-bar" id="key-bar">
    <input id="key-input" type="password" placeholder="Paste your API key here…" />
    <button onclick="saveKey()">Save key</button>
  </div>

  <!-- Tab nav -->
  <div class="tabs">
    <button class="tab-btn active" onclick="switchTab('search', this)">Search</button>
    <button class="tab-btn" onclick="switchTab('manage', this)">Manage files</button>
    <button class="tab-btn" onclick="switchTab('setup', this)">Get a key</button>
  </div>

  <!-- ── Search tab ── -->
  <div id="tab-search" class="tab-panel active">
    <div class="search-bar">
      <input id="query" type="text" placeholder="What do you want to find?"
        onkeydown="if(event.key==='Enter')doSearch()" />
      <button onclick="doSearch()">Search</button>
    </div>
    <div class="options">
      <label><input type="checkbox" id="synthesize"> AI answer (slower)</label>
      <button class="secondary" onclick="clearKey()" style="margin-left:auto;padding:6px 12px;font-size:0.8rem">Change key</button>
    </div>
    <div id="search-output"></div>
  </div>

  <!-- ── Manage tab ── -->
  <div id="tab-manage" class="tab-panel">

    <!-- Drop zone -->
    <div class="drop-zone" id="drop-zone"
      ondragover="onDragOver(event)" ondragleave="onDragLeave(event)" ondrop="onDrop(event)"
      onclick="document.getElementById('file-pick').click()">
      <p>Drag &amp; drop files here, or click to select</p>
      <div class="pick-btns" onclick="event.stopPropagation()">
        <button class="secondary" onclick="document.getElementById('file-pick').click()">Select files</button>
        <button class="secondary" onclick="document.getElementById('folder-pick').click()">Select folder</button>
      </div>
      <p class="drop-hint">Supported: .pdf · .docx · .txt · .md · .png · .jpg · .jpeg</p>
    </div>
    <input type="file" id="file-pick" multiple accept=".pdf,.docx,.txt,.md,.markdown,.png,.jpg,.jpeg"
      style="display:none" onchange="onFilePick(this.files)">
    <input type="file" id="folder-pick" webkitdirectory multiple
      style="display:none" onchange="onFilePick(this.files)">

    <!-- File list -->
    <div class="file-list" id="file-list-box" style="display:none">
      <div class="file-list-title">Selected files</div>
      <div id="file-list-items"></div>
    </div>

    <!-- Index button -->
    <div class="index-btn-row" id="index-btn-row" style="display:none">
      <button id="index-btn" onclick="doIndex()">Index files</button>
    </div>

    <!-- Progress -->
    <div class="progress-wrap" id="progress-wrap">
      <div class="progress-label" id="progress-label">Processing…</div>
      <div class="progress-bar-bg">
        <div class="progress-bar-fill" id="progress-bar"></div>
      </div>
    </div>

    <!-- Completion summary -->
    <div class="summary" id="summary"></div>

    <!-- Manage errors -->
    <div id="manage-error"></div>

    <!-- Danger zone -->
    <div class="danger-zone">
      <div class="danger-title">Danger zone</div>
      <p class="danger-desc">Deletes all indexed chunks for your key. This cannot be undone.</p>
      <button class="danger" onclick="openResetModal()">Reset index</button>
    </div>
  </div>
</div>

  <!-- ── Get a key tab ── -->
  <div id="tab-setup" class="tab-panel">

    <!-- New key via invite code -->
    <div class="key-section">
      <h3>New user — get a key</h3>
      <p>Enter the invite code your server host gave you. You'll get a personal key tied to your own private index.</p>
      <div class="key-row">
        <input id="invite-input" type="password" placeholder="Invite code…" />
        <button onclick="doGetKey()">Get key</button>
      </div>
      <div id="invite-error" class="inline-error"></div>
      <div class="key-reveal" id="new-key-reveal">
        <div class="key-reveal-label">Your key</div>
        <div class="key-reveal-value" id="new-key-value"></div>
        <p class="key-warning">&#9888; Save this somewhere safe — a password manager, notes app, or written down. This is the only time it will be shown. If you lose it, your indexed data becomes inaccessible.</p>
        <button onclick="saveNewKey()">Save key &amp; start using *search</button>
      </div>
    </div>

    <hr class="divider">

    <!-- Existing key from another device -->
    <div class="key-section">
      <h3>Already have a key — new device or browser</h3>
      <p>Your key is stored per-browser. Paste it here to use your existing index on this device.</p>
      <div class="key-row">
        <input id="existing-key-input" type="password" placeholder="Paste your key…" />
        <button onclick="saveExistingKey()">Use this key</button>
      </div>
      <div id="existing-key-error" class="inline-error"></div>
    </div>

    <hr class="divider">

    <!-- Switch persona -->
    <div class="key-section">
      <h3>Switch key</h3>
      <p>You can hold multiple keys for separate document collections — a personal index and a work index, for example. Switching here changes which collection you search and index into.</p>
      <button class="secondary" onclick="clearKey(); switchTab('setup', document.querySelector(&#39;.tab-btn:last-child&#39;))">Clear current key</button>
    </div>
  </div>

<!-- Reset confirmation modal -->
<div class="modal-overlay" id="reset-modal">
  <div class="modal">
    <h3>Reset index</h3>
    <p>This will permanently delete all indexed chunks for your key. Type <strong>DELETE</strong> to confirm.</p>
    <input type="text" id="confirm-input" placeholder="Type DELETE" oninput="onConfirmInput()" />
    <div class="modal-btns">
      <button class="secondary" onclick="closeResetModal()">Cancel</button>
      <button class="danger" id="confirm-reset-btn" onclick="doReset()" disabled>Reset index</button>
    </div>
  </div>
</div>

<script>
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
  } catch (e) {
    errEl.textContent = 'Request failed: ' + e.message;
  }
}

function saveNewKey() {
  if (!window._pendingNewKey) return;
  localStorage.setItem('ss_key', window._pendingNewKey);
  window._pendingNewKey = null;
  renderKeyBar();
  switchTab('search', document.querySelector('.tab-btn'));
}

async function saveExistingKey() {
  const k = document.getElementById('existing-key-input').value.trim();
  const errEl = document.getElementById('existing-key-error');
  errEl.textContent = '';
  if (!k) { errEl.textContent = 'Paste your key first.'; return; }

  // Validate key works by attempting a search with empty query (fast)
  try {
    const r = await fetch(API + '/search', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({key: k, query: 'test', limit: 1}),
    });
    if (!r.ok) { errEl.textContent = 'Server error — key not accepted.'; return; }
    localStorage.setItem('ss_key', k);
    renderKeyBar();
    switchTab('search', document.querySelector('.tab-btn'));
  } catch (e) {
    errEl.textContent = 'Could not reach server: ' + e.message;
  }
}

// ── Key bar ────────────────────────────────────────────────────────────────
function saveKey() {
  const k = document.getElementById('key-input').value.trim();
  if (!k) return;
  localStorage.setItem('ss_key', k);
  renderKeyBar();
}

function clearKey() {
  localStorage.removeItem('ss_key');
  renderKeyBar();
}

function renderKeyBar() {
  const k = localStorage.getItem('ss_key');
  const bar = document.getElementById('key-bar');
  if (k) {
    const short = k.slice(0, 8) + '…';
    bar.innerHTML = '<span class="key-saved">&#10003; Key saved (' + short + ')</span>'
      + '<button class="secondary" onclick="switchTab(\'setup\', document.querySelectorAll(\'.tab-btn\')[2])" style="padding:6px 12px;font-size:0.8rem">Switch key</button>';
  } else {
    bar.innerHTML = '<span style="color:#666;font-size:0.85rem;align-self:center;">No key saved —</span>'
      + '<button class="secondary" onclick="switchTab(\'setup\', document.querySelectorAll(\'.tab-btn\')[2])" style="padding:6px 12px;font-size:0.8rem">Get a key</button>';
  }
}

// ── Search ─────────────────────────────────────────────────────────────────
async function doSearch() {
  const key = localStorage.getItem('ss_key');
  const query = document.getElementById('query').value.trim();
  const synthesize = document.getElementById('synthesize').checked;
  const out = document.getElementById('search-output');

  if (!key) { out.innerHTML = '<p class="error">No key saved — paste your key above first.</p>'; return; }
  if (!query) return;

  out.innerHTML = '<p class="status">Searching…</p>';

  try {
    const r = await fetch(API + '/search', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({key, query, limit: 5, synthesize}),
    });
    if (!r.ok) {
      out.innerHTML = '<p class="error">Server error (' + r.status + ').</p>';
      return;
    }
    const data = await r.json();

    if (!data.results.length) {
      out.innerHTML = '<p class="status">No results — index some files first.</p>';
      return;
    }

    let html = '';
    if (data.answer) {
      html += '<div class="answer"><div class="answer-label">AI answer</div>' + escHtml(data.answer) + '</div>';
    }
    for (const res of data.results) {
      html += '<div class="result">'
        + '<div class="result-meta">'
        + '<span class="result-source">' + escHtml(res.source) + '</span>'
        + '<span class="result-score">' + res.score + '</span>'
        + '</div>'
        + '<div class="result-text">' + escHtml(res.text) + '</div>'
        + '</div>';
    }
    out.innerHTML = html;
  } catch (e) {
    out.innerHTML = '<p class="error">Request failed: ' + escHtml(e.message) + '</p>';
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
    '<div class="file-item">' + escHtml(f.webkitRelativePath || f.name) + '</div>'
  ).join('');
  if (rest > 0) html += '<div class="file-more">and ' + rest + ' more…</div>';
  items.innerHTML = html;
}

// ── Chunking (mirrors Python) ──────────────────────────────────────────────
function chunkText(text, source) {
  const words = text.trim().split(/\\s+/);
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
    const key = localStorage.getItem('ss_key');
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
  const key = localStorage.getItem('ss_key');
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
  const key = localStorage.getItem('ss_key');
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
    .replace(/\n/g, '<br>');
}

// ── Boot ───────────────────────────────────────────────────────────────────
renderKeyBar();
const q = document.getElementById('query');
if (q) q.focus();
</script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
def ui():
    return HTML
