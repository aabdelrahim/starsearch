import os

import httpx
from fastapi import APIRouter
from schemas import SearchRequest
from crypto import decrypt_field
from vector_ops import embed, collection_exists, key_to_collection, qdrant, OLLAMA_URL

LLM_MODEL = os.environ.get("LLM_MODEL", "qwen3:32b")

router = APIRouter()


@router.post("/search")
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
            continue  # malformed or unencrypted legacy payload
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
