import hashlib
from pathlib import Path

import requests


def file_hash(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def upload_chunks(chunks: list[dict], api_url: str, key: str) -> int:
    r = requests.post(
        f"{api_url}/index",
        json={"key": key, "chunks": chunks},
        timeout=300,
    )
    r.raise_for_status()
    return r.json().get("indexed", 0)
