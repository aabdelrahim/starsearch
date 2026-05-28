from fastapi import APIRouter, HTTPException
from qdrant_client.models import PointStruct
from schemas import IndexRequest
from crypto import encrypt_field
from vector_ops import embed, ensure_collection, key_to_collection, stable_point_id, qdrant

MAX_CHUNKS    = 500
MAX_CHUNK_LEN = 10_000

router = APIRouter()


@router.post("/index")
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
        vector = await embed(chunk.text)
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
