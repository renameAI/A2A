"""청킹 — 출처 라벨을 유지하는 분할 (ING-02).

PDF는 페이지 단위로 텍스트를 뽑은 뒤 문단 경계 우선으로 ~2,000자 청크를 만든다.
청크 ID는 provenance 역추적(ING-04)의 키가 된다.
"""
import io
from dataclasses import dataclass

from pypdf import PdfReader

DEFAULT_CHUNK_CHARS = 2000


@dataclass
class Chunk:
    chunk_id: str    # 예: "a1:ir_deck#3"
    source: str      # 자산 라벨 (예: "a1:ir_deck")
    text: str


def pdf_to_text(data: bytes) -> str:
    reader = PdfReader(io.BytesIO(data))
    pages = []
    for i, page in enumerate(reader.pages):
        text = (page.extract_text() or "").strip()
        if text:
            pages.append(f"[p.{i + 1}]\n{text}")
    return "\n\n".join(pages)


def chunk_text(text: str, source: str,
               max_chars: int = DEFAULT_CHUNK_CHARS) -> list[Chunk]:
    """문단 경계 우선 분할. 문단이 max_chars를 넘으면 그 안에서 하드 분할."""
    paragraphs: list[str] = []
    for para in text.split("\n\n"):
        para = para.strip()
        if not para:
            continue
        while len(para) > max_chars:
            paragraphs.append(para[:max_chars])
            para = para[max_chars:]
        paragraphs.append(para)

    chunks: list[Chunk] = []
    buffer = ""
    for para in paragraphs:
        if buffer and len(buffer) + len(para) + 2 > max_chars:
            chunks.append(Chunk(f"{source}#{len(chunks) + 1}", source, buffer))
            buffer = para
        else:
            buffer = f"{buffer}\n\n{para}" if buffer else para
    if buffer:
        chunks.append(Chunk(f"{source}#{len(chunks) + 1}", source, buffer))
    return chunks
