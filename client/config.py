import json
import logging
import os
import sys
from pathlib import Path

CONFIG_FILE   = Path.home() / ".starsearch" / "config.json"
STATE_FILE    = Path.home() / ".starsearch" / "state.json"
WATCH_FOLDER  = os.environ.get("STARSEARCH_FOLDER", str(Path.home() / "starsearch-docs"))
CHUNK_SIZE    = 500
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


def load_state() -> dict:
    return json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))
