"""IR덱 PDF → 페이지 렌더링 (질문 위치 탐지용, bbox 기능 전용).

텍스트 추출(chunking.pdf_to_text)과는 별개 경로 — 그쪽은 판단 재료(텍스트),
이쪽은 사람이 눈으로 대조할 원문 이미지 + VLM 전송용 이미지다.
PyMuPDF는 순수 파이썬 휠이라 외부 바이너리 없이 어디서든 돌아간다.
"""
from dataclasses import dataclass

import fitz  # PyMuPDF

MAX_PAGES = 12   # 비용 상한 — 표지·서머리에 근거가 몰리므로 앞쪽만으로 충분


@dataclass
class PageRender:
    page_no: int      # 1-base
    png: bytes        # UI 표시·디스크 저장용 (무손실)
    api_image: bytes  # VLM 전송용 — PNG/JPEG 중 작은 쪽 (전송 바이트 최소화)
    api_mime: str     # "image/png" | "image/jpeg"
    text: str         # 텍스트 레이어 — quote 그라운딩 검증용 (스캔 PDF는 "")


def render_pdf_pages(data: bytes, dpi: int = 150, max_pages: int = MAX_PAGES,
                     jpeg_quality: int = 80) -> list[PageRender]:
    """PDF 바이트 → PageRender 리스트.

    전송용 이미지는 페이지별로 PNG와 JPEG를 모두 인코딩해 작은 쪽을 고른다 —
    텍스트 위주 슬라이드는 PNG가, 사진 위주 슬라이드는 JPEG가 작다.
    텍스트 레이어가 빈 문자열이면(스캔 PDF) 그라운딩 검증은 '불가'로 처리된다."""
    doc = fitz.open(stream=data, filetype="pdf")
    try:
        zoom = dpi / 72
        mat = fitz.Matrix(zoom, zoom)
        pages: list[PageRender] = []
        for page in doc:
            if len(pages) >= max_pages:
                break
            pix = page.get_pixmap(matrix=mat)
            png = pix.tobytes("png")
            jpg = pix.tobytes("jpg", jpg_quality=jpeg_quality)
            api_image, api_mime = ((jpg, "image/jpeg") if len(jpg) < len(png)
                                   else (png, "image/png"))
            pages.append(PageRender(
                page_no=len(pages) + 1, png=png,
                api_image=api_image, api_mime=api_mime,
                text=page.get_text("text")))
        return pages
    finally:
        doc.close()
