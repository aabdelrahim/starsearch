import base64
import hashlib
import secrets

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def _enc_key(api_key: str) -> bytes:
    return hashlib.sha256(f"starsearch-enc-v1:{api_key}".encode()).digest()


def encrypt_field(api_key: str, plaintext: str) -> str:
    """AES-GCM encrypt a string field; returns base64-encoded nonce+ciphertext."""
    aesgcm = AESGCM(_enc_key(api_key))
    nonce = secrets.token_bytes(12)
    ct = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
    return base64.b64encode(nonce + ct).decode("ascii")


def decrypt_field(api_key: str, encoded: str) -> str:
    """Decrypt a field produced by encrypt_field."""
    aesgcm = AESGCM(_enc_key(api_key))
    raw = base64.b64decode(encoded)
    nonce, ct = raw[:12], raw[12:]
    return aesgcm.decrypt(nonce, ct, None).decode("utf-8")
