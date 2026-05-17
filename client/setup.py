"""
*search (Starsearch) — one-click setup
Run this once. No Ollama needed — embedding runs on the server.
"""

import json
import platform
import subprocess
import sys
from pathlib import Path

CONFIG_FILE  = Path.home() / ".starsearch" / "config.json"
DOCS_FOLDER  = Path.home() / "starsearch-docs"

def title(t): print(f"\n{'─'*50}\n  {t}\n{'─'*50}")
def ok(t):    print(f"  ✓  {t}")
def info(t):  print(f"  →  {t}")
def warn(t):  print(f"  !  {t}")
def ask(prompt, default=""):
    val = input(f"\n  {prompt}" + (f" [{default}]" if default else "") + ": ").strip()
    return val if val else default

def is_windows(): return platform.system() == "Windows"
def is_mac():     return platform.system() == "Darwin"


def check_python():
    title("Checking Python")
    v = sys.version_info
    if v.major < 3 or (v.major == 3 and v.minor < 10):
        warn(f"Python {v.major}.{v.minor} found — 3.10+ required.")
        warn("Download: https://www.python.org/downloads/")
        warn("Tick 'Add Python to PATH' during install.")
        input("\nPress Enter to exit...")
        sys.exit(1)
    ok(f"Python {v.major}.{v.minor}.{v.micro}")


def install_packages():
    title("Installing required packages")
    req = Path(__file__).parent / "requirements.txt"
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-r", str(req), "--quiet"],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        ok("All packages installed")
    else:
        warn("Package install failed. Fix this before continuing:")
        warn(f'  pip install -r "{req}"')
        input("\nPress Enter to exit...")
        sys.exit(1)


def check_tesseract():
    title("Checking Tesseract (image / handwriting support — optional)")
    result = subprocess.run("tesseract --version", shell=True, capture_output=True)
    if result.returncode == 0:
        ok("Tesseract found — image search enabled")
    else:
        info("Tesseract not found — PDFs, Word docs, and text files will still work.")
        info("To add image/photo support: https://github.com/UB-Mannheim/tesseract/wiki")


def configure():
    title("Server configuration")

    existing = {}
    if CONFIG_FILE.exists():
        existing = json.loads(CONFIG_FILE.read_text())
        info(f"Existing config: {existing.get('api_url','?')}")
        if ask("Keep existing config? (yes/no)", "yes").lower().startswith("y"):
            ok("Keeping existing config")
            return existing

    print()
    print("  Your server host will give you two things:")
    print("  1. The API URL  (e.g. https://search.example.com  or  http://100.x.x.x:6400)")
    print("  2. Your API key (a long random string)")
    print()

    api_url = ask("API URL", existing.get("api_url", ""))
    while not api_url.startswith("http"):
        warn("Should start with http:// or https://")
        api_url = ask("API URL")

    key = ask("Your API key", existing.get("key", ""))
    while not key:
        warn("Key cannot be empty")
        key = ask("Your API key")

    config = {"api_url": api_url.rstrip("/"), "key": key}
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(config, indent=2))
    ok(f"Config saved to {CONFIG_FILE}")
    return config


def create_docs_folder():
    title("Documents folder")
    DOCS_FOLDER.mkdir(parents=True, exist_ok=True)
    ok(f"Ready: {DOCS_FOLDER}")
    info("Drop files here — they'll be indexed automatically when the watcher runs.")


def create_shortcut(config):
    title("Creating start shortcut")
    watcher = Path(__file__).parent / "watcher.py"

    if is_windows():
        bat = Path(__file__).parent / "Start Starsearch.bat"
        bat.write_text(
            f'@echo off\n'
            f'title *search watcher\n'
            f'"{sys.executable}" "{watcher}"\n'
            f'pause\n'
        )
        ok(f'Created: "{bat}"')
        info('Double-click "Start Starsearch.bat" to run the watcher.')

    else:
        sh = Path(__file__).parent / "start_starsearch.sh"
        sh.write_text(f'#!/bin/bash\n"{sys.executable}" "{watcher}"\n')
        sh.chmod(0o755)
        ok(f"Created: {sh}")
        info("Run ./start_starsearch.sh to start the watcher.")


def done():
    title("Setup complete!")
    print(f"""
  1. Drop documents into:   {DOCS_FOLDER}
     (PDF, Word, text, markdown, images)

  2. Start the watcher:     double-click "Start Starsearch.bat"
     Leave it running in the background.

  3. Search:                open the URL your server host gave you
     Your API key is already saved — just type and search.
""")


def main():
    print("""
  ╔══════════════════════════════════╗
  ║   *search — Starsearch Setup     ║
  ╚══════════════════════════════════╝
""")
    check_python()
    install_packages()
    check_tesseract()
    config = configure()
    create_docs_folder()
    create_shortcut(config)
    done()
    input("  Press Enter to exit...")


if __name__ == "__main__":
    main()
