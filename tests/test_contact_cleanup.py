"""접점 정제 — 값은 '닿을 수 있는 것'이어야 한다.

실측(할리케이 일본 후보): 일본어 사이트에서 `お問い合わせ: お問い合わせ`가 나왔다.
링크의 글자를 값으로 옮긴 것이라 클릭할 게 없다. 프롬프트로만 막으면 다국어
사이트에서 계속 새어 나오므로 코드가 정리한다.
"""
from app.engine.company_ontology import _clean_contacts


def test_relative_paths_become_absolute():
    c = _clean_contacts([{"channel": "문의 폼", "value": "/contact", "role_hint": ""}],
                        "https://x.jp/about")
    assert c[0].value == "https://x.jp/contact"


def test_label_repeated_as_value_is_demoted_to_a_desk_not_dropped():
    """문이 있다는 사실은 정보다 — 버리지 않고 창구 안내로 남긴다."""
    c = _clean_contacts([{"channel": "お問い合わせ", "value": "お問い合わせ",
                          "role_hint": ""}], "https://x.jp/")
    assert len(c) == 1 and c[0].role_hint == "お問い合わせ"


def test_reachable_values_are_untouched():
    rows = [{"channel": "대표 메일", "value": "a@b.com", "role_hint": ""},
            {"channel": "전화", "value": "03-1234-5678", "role_hint": ""},
            {"channel": "문의 폼", "value": "https://x.com/c", "role_hint": "구매팀"}]
    c = _clean_contacts(rows, "https://x.com")
    assert [x.value for x in c] == ["a@b.com", "03-1234-5678", "https://x.com/c"]
    assert c[2].role_hint == "구매팀"


def test_department_only_value_keeps_its_role_hint():
    c = _clean_contacts([{"channel": "문의", "value": "商品統括部",
                          "role_hint": "商品統括部"}], "https://x.jp")
    assert c[0].value == "商品統括部" and c[0].role_hint == "商品統括部"


def test_empty_values_are_dropped():
    assert _clean_contacts([{"channel": "x", "value": "  ", "role_hint": ""}], "") == []
