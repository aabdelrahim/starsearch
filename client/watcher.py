"""
*search (Starsearch) — client watcher
Watches a folder, extracts text, sends chunks to the Starsearch API for embedding.
No Ollama required on this machine — embedding runs server-side.
"""

import time
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from config import SUPPORTED_EXTENSIONS, WATCH_FOLDER, load_config, load_state, save_state, log
from extractors import extract_text
from chunking import chunk_text
from api_client import file_hash, upload_chunks


def ingest_file(path: Path, api_url: str, key: str, state: dict, watch_root: Path) -> bool:
    if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        return False

    h = file_hash(path)
    if state.get(str(path)) == h:
        return False

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
        if path.is_file() and ingest_file(path, api_url, key, state, folder):
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
