from __future__ import annotations

from dataclasses import dataclass

from .models import NormalizedDocument


@dataclass(frozen=True)
class TextChunk:
    chunk_id: str
    doc_id: str
    text: str


def chunk_documents(documents: list[NormalizedDocument], chunk_size: int = 2200, overlap: int = 250) -> list[TextChunk]:
    chunks: list[TextChunk] = []
    for document in documents:
        text = document.text.strip()
        if not text:
            continue
        start = 0
        index = 0
        while start < len(text):
            end = min(len(text), start + chunk_size)
            chunk_text = text[start:end]
            chunks.append(TextChunk(chunk_id=f"{document.doc_id}-{index}", doc_id=document.doc_id, text=chunk_text))
            if end >= len(text):
                break
            start = max(end - overlap, start + 1)
            index += 1
    return chunks

