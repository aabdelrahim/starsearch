from pathlib import Path
from config import log, SUPPORTED_EXTENSIONS


def extract_text(path: Path) -> str:
    ext = path.suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        return ""
    try:
        if ext == ".pdf":
            from pypdf import PdfReader
            return "\n".join(p.extract_text() or "" for p in PdfReader(str(path)).pages)

        if ext == ".docx":
            from docx import Document
            return "\n".join(p.text for p in Document(str(path)).paragraphs)

        if ext in (".png", ".jpg", ".jpeg"):
            import pytesseract
            from PIL import Image
            return pytesseract.image_to_string(Image.open(str(path)))

        if ext in (".txt", ".md"):
            return path.read_text(encoding="utf-8", errors="ignore")

    except Exception as e:
        log.warning(f"Could not extract text from {path.name}: {e}")
    return ""
