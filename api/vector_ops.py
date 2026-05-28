import hashlib
import os

import httpx
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import Distance, VectorParams

QDRANT_URL  = os.environ.get("QDRANT_URL",  "http://qdrant:6333")
OLLAMA_URL  = os.environ.get("OLLAMA_URL",  "http://host-gateway:11434")
EMBED_MODEL = os.environ.get("EMBED_MODEL", "nomic-embed-text")
VECTOR_SIZE = 768  # nomic-embed-text

qdrant = AsyncQdrantClient(url=QDRANT_URL, timeout=30)


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


async def ensure_collection(name: str) -> None:
    existing = {c.name for c in (await qdrant.get_collections()).collections}
    if name not in existing:
        await qdrant.create_collection(
            collection_name=name,
            vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
        )


async def collection_exists(name: str) -> bool:
    existing = {c.name for c in (await qdrant.get_collections()).collections}
    return name in existing
