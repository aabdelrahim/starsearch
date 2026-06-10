"""
Starsearch API
Key-based vector search proxy with at-rest encryption.
Chunk text and source are AES-GCM encrypted in Qdrant payloads;
vectors are generated from plaintext so search still works.

Env vars:
  QDRANT_URL      default http://qdrant:6333
  OLLAMA_URL      default http://100.114.115.13:11434
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
from fastapi.responses import FileResponse
from pathlib import Path
from PIL import Image
import pytesseract
from pydantic import BaseModel
import httpx
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

QDRANT_URL    = os.environ.get("QDRANT_URL",    "http://qdrant:6333")
EMBED_URL     = os.environ.get("EMBED_URL",     "http://ollama-embed:11434")  # local, always-on
OLLAMA_URL    = os.environ.get("OLLAMA_URL",    "http://192.168.50.93:11434") # anteframe, synthesis only
EMBED_MODEL   = os.environ.get("EMBED_MODEL",   "nomic-embed-text")
LLM_MODEL     = os.environ.get("LLM_MODEL",     "qwen3:32b")
SIGNUP_SECRET = os.environ.get("SIGNUP_SECRET", "")
CORTEX_URL    = os.environ.get("CORTEX_URL",    "http://cortex:3000")

# Collections that cannot be cleared or have sources deleted (comma-separated collection hashes)
_protected_raw = os.environ.get("PROTECTED_COLLECTIONS", "ss_a7e8fa2d1419abbade45688cdf6aea87")
PROTECTED_COLLECTIONS: set[str] = {c.strip() for c in _protected_raw.split(",") if c.strip()}

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

NO_CACHE = {"Cache-Control": "no-store"}

@app.get("/")
def ui():
    return FileResponse("static/index.html", headers=NO_CACHE)

@app.get("/static/{filename}")
def static_file(filename: str):
    path = Path("static") / filename
    if not path.exists() or not path.is_file():
        from fastapi import HTTPException
        raise HTTPException(status_code=404)
    return FileResponse(str(path), headers=NO_CACHE)


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
            f"{EMBED_URL}/api/embeddings",
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
    result = await qdrant.query_points(collection_name=collection, query=query_vector, limit=req.limit)
    hits = result.points

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


@app.post("/fetch")
async def fetch_url(key: str = Form(...), url: str = Form(...)):
    """Fetch a URL server-side, strip HTML boilerplate, return clean text for client-side indexing."""
    if not key or len(key) < 8:
        raise HTTPException(status_code=403, detail="Invalid key")
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="URL must start with http:// or https://")
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True,
                                     headers={"User-Agent": "starsearch/1.0"}) as h:
            r = await h.get(url)
            r.raise_for_status()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not fetch URL: {str(e)[:120]}")

    from bs4 import BeautifulSoup
    import re
    soup = BeautifulSoup(r.text, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "aside", "noscript"]):
        tag.decompose()
    text = soup.get_text(separator=" ", strip=True)
    text = re.sub(r"\s+", " ", text).strip()
    title = soup.title.string.strip() if soup.title and soup.title.string else url

    return {"text": text, "url": url, "title": title}


@app.post("/extract")
async def extract(key: str = Form(...), file: UploadFile = File(...)):
    """Accept an image upload, run OCR, return extracted text."""
    if not key or len(key) < 8:
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
    if collection in PROTECTED_COLLECTIONS:
        raise HTTPException(status_code=403, detail="This is a read-only demo collection.")
    if await collection_exists(collection):
        await qdrant.delete_collection(collection_name=collection)
    return {"cleared": True}


class ValidateRequest(BaseModel):
    key: str

@app.post("/validate")
async def validate(req: ValidateRequest):
    collection = key_to_collection(req.key)
    exists = await collection_exists(collection)
    return {"valid": exists, "protected": collection in PROTECTED_COLLECTIONS}


class ListRequest(BaseModel):
    key: str

@app.post("/list")
async def list_sources(req: ListRequest):
    """Return all unique indexed sources with chunk counts, decrypted."""
    collection = key_to_collection(req.key)
    if not await collection_exists(collection):
        return {"sources": []}

    sources: dict[str, int] = {}
    offset = None
    while True:
        result, next_offset = await qdrant.scroll(
            collection_name=collection,
            limit=250,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        for point in result:
            try:
                source = decrypt_field(req.key, point.payload["source"])
                sources[source] = sources.get(source, 0) + 1
            except Exception:
                pass
        if next_offset is None:
            break
        offset = next_offset

    return {"sources": sorted(
        [{"source": s, "chunks": c} for s, c in sources.items()],
        key=lambda x: x["source"]
    )}


class FileRequest(BaseModel):
    key: str
    source: str

@app.post("/file")
async def get_file(req: FileRequest):
    """Reconstruct a full source document from its chunks.

    Source is AES-GCM encrypted with a per-field random nonce, so Qdrant can't
    filter on it server-side — scroll the whole collection, decrypt each point's
    source, keep matches, order by chunk_index, and stitch back into the text.
    """
    collection = key_to_collection(req.key)
    if not await collection_exists(collection):
        raise HTTPException(status_code=403, detail="Invalid key")

    matches: list[tuple[int, str]] = []
    offset = None
    while True:
        result, next_offset = await qdrant.scroll(
            collection_name=collection,
            limit=250,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        for point in result:
            try:
                source = decrypt_field(req.key, point.payload["source"])
            except Exception:
                continue
            if source != req.source:
                continue
            try:
                text = decrypt_field(req.key, point.payload["text"])
            except Exception:
                continue
            matches.append((point.payload.get("chunk_index", 0), text))
        if next_offset is None:
            break
        offset = next_offset

    if not matches:
        raise HTTPException(status_code=404, detail="Source not found")

    matches.sort(key=lambda m: m[0])
    return {
        "source": req.source,
        "chunks": len(matches),
        "text": "\n\n".join(t for _, t in matches),
    }


class DeleteSourceRequest(BaseModel):
    key: str
    source: str

@app.delete("/index")
async def delete_source(req: DeleteSourceRequest):
    """Remove all chunks for a specific source file from the index."""
    collection = key_to_collection(req.key)
    if collection in PROTECTED_COLLECTIONS:
        raise HTTPException(status_code=403, detail="This is a read-only demo collection.")
    if not await collection_exists(collection):
        return {"deleted": 0}

    to_delete = []
    offset = None
    while True:
        result, next_offset = await qdrant.scroll(
            collection_name=collection,
            limit=250,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        for point in result:
            try:
                source = decrypt_field(req.key, point.payload["source"])
                if source == req.source:
                    to_delete.append(point.id)
            except Exception:
                pass
        if next_offset is None:
            break
        offset = next_offset

    if to_delete:
        from qdrant_client.models import PointIdsList
        await qdrant.delete(
            collection_name=collection,
            points_selector=PointIdsList(points=to_delete),
        )
    return {"deleted": len(to_delete)}


# ─── AI start + warmup ────────────────────────────────────────────────────────


@app.post("/ai/start")
async def ai_start(model: str = "qwen3:32b", warmup: bool = True):
    """Start Ollama on anteframe and load the synthesis model. warmup=true (default) pre-loads into VRAM."""
    try:
        params = f"?model={model}" if warmup else "?model="
        async with httpx.AsyncClient(timeout=12) as h:
            r = await h.post(f"{CORTEX_URL}/api/ollama/start{params}")
            return r.json()
    except Exception as e:
        return {"ok": False, "error": str(e)[:80]}


# ─── Health ───────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    async def check(url: str) -> bool:
        try:
            async with httpx.AsyncClient(timeout=3) as h:
                r = await h.get(f"{url}/api/tags")
                return r.status_code == 200
        except Exception:
            return False
    embed_ok, synthesis_ok = await asyncio.gather(check(EMBED_URL), check(OLLAMA_URL))
    return {"embed": embed_ok, "synthesis": synthesis_ok}


