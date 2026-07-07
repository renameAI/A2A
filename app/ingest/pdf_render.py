"""IR덱 PDF → 페이지 PNG 렌더링 (근거 시각화용, bbox 기능 전용).

텍스트 추출(chunking.pdf_to_text)과는 별개 경로 — 그쪽은 판단 재료(텍스트),
이쪽은 사람이 눈으로 대조할 원문 이미지다. PyMuPDF는 순수 파이썬 휠이라
LibreOffice 같은 외부 바이너리 없이 어디서든 돌아간다.
"""
import fitz  # PyMuPDF

MAX_PAGES = 12   # 비용 상한 — 표지·서머리에 근거가 몰리므로 앞쪽만으로 충분


def render_pdf_pages(data: bytes, dpi: int = 150,
                     max_pages: int = MAX_PAGES) -> list[bytes]:
    """PDF 바이트 → 페이지별 PNG 바이트 리스트 (1-base 순서 유지)."""
    doc = fitz.open(stream=data, filetype="pdf")
    try:
        zoom = dpi / 72
        mat = fitz.Matrix(zoom, zoom)
        images = []
        for page in doc:
            if len(images) >= max_pages:
                break
            pix = page.get_pixmap(matrix=mat)
            images.append(pix.tobytes("png"))
        return images
    finally:
        doc.close()
