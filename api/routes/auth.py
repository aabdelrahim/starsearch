import os
import secrets

from fastapi import APIRouter, Header, HTTPException
from vector_ops import ensure_collection, key_to_collection

router = APIRouter()
SIGNUP_SECRET = os.environ.get("SIGNUP_SECRET", "")


@router.post("/signup")
async def signup(authorization: str = Header(default="")):
    if SIGNUP_SECRET and authorization != f"Bearer {SIGNUP_SECRET}":
        raise HTTPException(status_code=403, detail="Forbidden")
    key = secrets.token_urlsafe(32)
    await ensure_collection(key_to_collection(key))
    return {
        "key": key,
        "note": "Save this key — it cannot be recovered. Lose it and your index is inaccessible.",
    }
