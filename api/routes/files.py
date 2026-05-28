import io

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from PIL import Image
import pytesseract
from schemas import ClearRequest
from vector_ops import collection_exists, key_to_collection, qdrant

router = APIRouter()


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


@router.post("/clear")
async def clear(req: ClearRequest):
    """Delete the user's Qdrant collection if it exists."""
    collection = key_to_collection(req.key)
    if await collection_exists(collection):
        await qdrant.delete_collection(collection_name=collection)
    return {"cleared": True}
