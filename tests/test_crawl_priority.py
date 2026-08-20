"""크롤 우선순위 — 아웃리치에 쓸모 있는 페이지가 목록에 들어와야 한다.

실측(Megamart): 키워드가 동등 가중이던 동안 5페이지 중 3장이 /product/1428…
상품 상세였고 연락처·입점 안내는 한 장도 안 들어왔다. 'product'는 링크 텍스트와
경로에 반복돼 점수가 쉽게 쌓이는데, 정작 필요한 것은 접점과 타이밍 신호다.
"""
from app.ingest.crawler import _PRIORITY_WEIGHTS, _priority_links


def _html(*paths):
    links = "".join(f'<a href="{p}">{p}</a>' for p in paths)
    return f"<html><body>{links}</body></html>"


def test_outreach_pages_outrank_product_detail():
    html = _html("/product/1", "/product/2", "/product/3",
                 "/contact", "/recruit")
    out = _priority_links(html, "https://x.com/")
    assert out[0].endswith("/contact")
    assert any(u.endswith("/recruit") for u in out[:3])


def test_one_kind_cannot_fill_the_list():
    """상품 상세 여섯 장보다 연락처 한 장이 쓸모 있다."""
    html = _html(*[f"/product/{i}" for i in range(6)], "/vendor/list")
    out = _priority_links(html, "https://x.com/")
    assert sum(1 for u in out if "/product/" in u) <= 2
    assert any("vendor" in u for u in out)


def test_contact_and_partner_are_the_top_weights():
    """접점이 최우선 — '닿기'가 제품의 약속이다."""
    top = max(_PRIORITY_WEIGHTS.values())
    for k in ("contact", "문의", "partner", "입점", "납품"):
        assert _PRIORITY_WEIGHTS[k] == top, k
    for k in ("product", "제품"):
        assert _PRIORITY_WEIGHTS[k] < _PRIORITY_WEIGHTS["채용"]


def test_multilingual_paths_are_matched():
    html = _html("/採用", "/お問い合わせ", "/회사소개")
    out = _priority_links(html, "https://x.jp/")
    assert len(out) == 3


def test_external_and_self_links_are_excluded():
    html = ('<a href="https://other.com/contact">x</a>'
            '<a href="https://x.com/">home</a>'
            '<a href="/contact">c</a>')
    out = _priority_links(html, "https://x.com/")
    assert out == ["https://x.com/contact"]
