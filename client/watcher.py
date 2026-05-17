"""
*search (Starsearch) — client watcher
Watches a folder, extracts text, sends chunks to the Starsearch API for embedding.
No Ollama required on this machine — embedding runs server-side.
"""

import hashlib
import json
import logging
import os
import sys
import time
from pathlib import Path

import requests
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

# ─── Config ───────────────────────────────────────────────────────────────────

CONFIG_FILE   = Path.home() / ".starsearch" / "config.json"
STATE_FILE    = Path.home() / ".starsearch" / "state.json"
WATCH_FOLDER  = os.environ.get("STARSEARCH_FOLDER", str(Path.home() / "starsearch-docs"))
CHUNK_SIZE    = 500   # words
CHUNK_OVERLAP = 50

SUPPORTED_EXTENSIONS = {".pdf", ".txt", ".md", ".docx", ".png", ".jpg", ".jpeg"}

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("starsearch")


def load_config() -> dict:
    if not CONFIG_FILE.exists():
        log.error(f"No config found at {CONFIG_FILE}")
        log.error("Run setup.py first, or create the config manually.")
        sys.exit(1)
    return json.loads(CONFIG_FILE.read_text())


# ─── State ────────────────────────────────────────────────────────────────────

def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {}


def save_state(state: dict):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


def file_hash(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


# ─── Text extraction ──────────────────────────────────────────────────────────

def extract_text(path: Path) -> str:
    ext = path.suffix.lower()
    try:
        if ext == ".pdf":
            from pypdf import PdfReader
            return "\n".join(p.extract_text() or "" for p in PdfReader(str(path)).pages)

        elif ext == ".docx":
            from docx import Document
            return "\n".join(p.text for p in Document(str(path)).paragraphs)

        elif ext in (".png", ".jpg", ".jpeg"):
            import pytesseract
            from PIL import Image
            return pytesseract.image_to_string(Image.open(str(path)))

        elif ext in (".txt", ".md"):
            return path.read_text(encoding="utf-8", errors="ignore")

    except Exception as e:
        log.warning(f"Could not extract text from {path.name}: {e}")
    return ""


# ─── Chunking ─────────────────────────────────────────────────────────────────

def chunk_text(text: str, source: str) -> list[dict]:
    words = text.split()
    chunks, i, idx = [], 0, 0
    while i < len(words):
        chunk = " ".join(words[i:i + CHUNK_SIZE])
        if chunk.strip():
            chunks.append({"text": chunk, "source": source, "chunk_index": idx})
        i += CHUNK_SIZE - CHUNK_OVERLAP
        idx += 1
    return chunks


# ─── Upload ───────────────────────────────────────────────────────────────────

def upload_chunks(chunks: list[dict], api_url: str, key: str) -> int:
    r = requests.post(
        f"{api_url}/index",
        json={"key": key, "chunks": chunks},
        timeout=300,
    )
    r.raise_for_status()
    return r.json().get("indexed", 0)


# ─── Ingest one file ──────────────────────────────────────────────────────────

def ingest_file(path: Path, api_url: str, key: str, state: dict, watch_root: Path) -> bool:
    if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        return False

    h = file_hash(path)
    if state.get(str(path)) == h:
        return False  # unchanged

    log.info(f"Ingesting: {path.name}")
    text = extract_text(path)
    if not text.strip():
        log.warning(f"No text in {path.name} — skipping")
        return False

    try:
        source = str(path.relative_to(watch_root))
    except ValueError:
        source = path.name

    chunks = chunk_text(text, source)
    try:
        uploaded = upload_chunks(chunks, api_url, key)
    except Exception as e:
        log.error(f"Upload failed for {path.name}: {e}")
        return False

    state[str(path)] = h
    log.info(f"  → {uploaded} chunks indexed from {path.name}")
    return True


# ─── Watcher ──────────────────────────────────────────────────────────────────

class DocHandler(FileSystemEventHandler):
    def __init__(self, api_url: str, key: str, state: dict, watch_root: Path):
        self.api_url = api_url
        self.key = key
        self.state = state
        self.watch_root = watch_root

    def on_created(self, event):
        if not event.is_directory:
            time.sleep(0.5)
            if ingest_file(Path(event.src_path), self.api_url, self.key, self.state, self.watch_root):
                save_state(self.state)

    def on_modified(self, event):
        self.on_created(event)


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    cfg = load_config()
    api_url = cfg["api_url"].rstrip("/")
    key     = cfg["key"]
    folder  = Path(WATCH_FOLDER)
    folder.mkdir(parents=True, exist_ok=True)

    log.info("*search watcher starting")
    log.info(f"Folder : {folder}")
    log.info(f"Server : {api_url}")

    state = load_state()

    log.info("Scanning existing documents...")
    changed = False
    for path in folder.rglob("*"):
        if path.is_file():
            if ingest_file(path, api_url, key, state, folder):
                changed = True
    if changed:
        save_state(state)
    log.info("Scan complete. Watching for new files...")

    handler = DocHandler(api_url, key, state, folder)
    observer = Observer()
    observer.schedule(handler, str(folder), recursive=True)
    observer.start()

    try:
        while True:
            time.sleep(5)
    except KeyboardInterrupt:
        observer.stop()
        log.info("Starsearch stopped.")
    observer.join()


if __name__ == "__main__":
    main()
