from config import CHUNK_SIZE, CHUNK_OVERLAP


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
