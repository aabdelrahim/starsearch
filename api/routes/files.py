import io

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from PIL import Image
import pytesseract
from schemas import ClearRequest, FileRequest
from crypto import decrypt_field
from vector_ops import collection_exists, key_to_collection, qdrant

router = APIRouter()

SCROLL_PAGE = 256  # points per scroll page when reconstructing a document


@router.post("/extract")
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


@router.post("/file")
async def file(req: FileRequest):
    """Reconstruct a full source document.

    Source is AES-GCM encrypted with a per-field random nonce, so Qdrant can't
    filter on it server-side — we scroll the whole collection, decrypt each
    point's source, keep the chunks whose source matches, then order by
    chunk_index and stitch them back into the original text.
    """
    collection = key_to_collection(req.key)
    if not await collection_exists(collection):
        raise HTTPException(status_code=403, detail="Invalid key")

    matches: list[tuple[int, str]] = []
    offset = None
    while True:
        points, offset = await qdrant.scroll(
            collection_name=collection,
            limit=SCROLL_PAGE,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        for p in points:
            try:
                source = decrypt_field(req.key, p.payload["source"])
            except Exception:
                continue  # malformed / legacy / wrong-key payload
            if source != req.source:
                continue
            try:
                text = decrypt_field(req.key, p.payload["text"])
            except Exception:
                continue
            matches.append((p.payload.get("chunk_index", 0), text))
        if offset is None:
            break

    if not matches:
        raise HTTPException(status_code=404, detail="Source not found")

    matches.sort(key=lambda m: m[0])
    return {
        "source": req.source,
        "chunks": len(matches),
        "text": "\n\n".join(t for _, t in matches),
    }


@router.post("/clear")
async def clear(req: ClearRequest):
    """Delete the user's Qdrant collection if it exists."""
    collection = key_to_collection(req.key)
    if await collection_exists(collection):
        await qdrant.delete_collection(collection_name=collection)
    return {"cleared": True}
