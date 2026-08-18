"""웹 검색 결과 → 실제 기업 후보 추출 (이슈 #6-D 품질 수정).

배경(실측 2026-08): 검색 히트를 그대로 후보로 썼더니 후보 10곳 중 8곳이
기사·블로그였다 — "에너지 음료? 고카페인 음료! 이대로 괜찮은가?"가 회사명으로
떴다. 페이지 제목은 회사명이 아니고, 검색 결과는 기업이 아니다.

두 겹으로 거른다:
1) 도메인 필터(코드) — 블로그·위키·SNS·뉴스는 기업 페이지가 아니다. 결정적이라
   LLM 비용이 안 들고, 명백한 쓰레기를 LLM에 보내지 않아 추출 품질도 올라간다.
2) 기업 추출(LLM 1회) — 남은 히트에서 **실존하는 회사만** 뽑는다. 회사를 못
   찾으면 빈 배열을 반환한다(억지로 채우지 않는다 — RET-06과 같은 정신).
"""
from .prompts import HARD_RULES

# 기업 페이지가 아닌 도메인 — 부분 문자열로 검사한다.
_NON_COMPANY = (
    "blog.naver.com", "m.blog.naver.com", "cafe.naver.com", "post.naver.com",
    "tistory.com", "brunch.co.kr", "velog.io", "medium.com",
    "wikipedia.org", "namu.wiki", "namuwiki",
    "instagram.com", "facebook.com", "youtube.com", "youtu.be",
    "twitter.com", "x.com", "tiktok.com", "threads.net", "linkedin.com/posts",
    "news.naver.com", "n.news.naver.com", "daum.net/news",
    "coupang.com", "11st.co.kr", "gmarket.co.kr", "amazon.", "aliexpress.",
    "quora.com", "reddit.com", "pinterest.",
    "slideshare.net", "issuu.com", "scribd.com",
    "dbpia.co.kr", "riss.kr", "kci.go.kr",   # 논문·리포지토리
)


# ── 채택 결정 (결정=코드) ────────────────────────────────────────────
# 기대효용: EU(채택) = p·v − (1−p)·c
#   v = 실존 후보를 파이프라인에 올리는 가치 (놓치면 이 요청에서 다시 못 찾는다)
#   c = 오채택 정리 비용 (하류의 온톨로지 판독·재랭킹·사용자 반응이 걸러준다)
# 하류 필터가 3겹이라 c ≪ v — c/v = 1/4로 두면 채택 임계는
#   τ = c / (v + c) = 0.2   (EU > 0 ⇔ p > τ)
# 모델은 p만 산출하고 결정은 이 임계가 내린다. 근거: 2601.07767 — 모델의
# 자체 결정 정책은 자기 보정 신호를 활용하지 못한다(과잉 회피 실측과 일치).
import os as _os


def _accept_tau() -> float:
    return float(_os.environ.get("EXTRACT_ACCEPT_TAU", "0.2"))


# 출처 승수 — 모델이 판정한 source_kind를 p에 곱해 유효 확률 p_eff를 만든다.
#   p_eff = p · w(source_kind)
# 근거는 베이즈적이다: 스니펫이 '그 회사가 실존·부합한다'고 말할 때, 그 말의
# 화자가 회사 자신(own)이면 증거 강도가 가장 높고, 구조화된 3자(directory)는
# 중간, 스쳐 언급(mention)은 가장 약하다. 실측(할리케이 프로덕션 E2E): 최종
# 10곳 중 5곳이 뉴스 기사·채용공고·블로그에서 스쳐 언급된 회사(Etsy·Mytheresa·
# Vestiaire·Eco Brand Japan·Ethical-Clothing)였다 — 재랭킹은 보완성만 보고
# 출처 신뢰도를 몰라 걸러지지 않았다.
# 값은 하드코딩된 업종 어휘가 아니라 **증거 강도의 서열**이다. 실 답장률
# (B4 원장)이 쌓이면 데이터로 재조정한다.
_SOURCE_W = {"own": 1.0, "directory": 0.85, "mention": 0.55}


def _source_weight(kind: str) -> float:
    return _SOURCE_W.get((kind or "").strip().lower(), 0.7)   # 미상은 중간


def filter_company_hits(hits: list[dict]) -> tuple[list[dict], int]:
    """비기업 도메인 제거. (남은 히트, 제외 수)."""
    kept = [h for h in hits
            if not any(bad in (h.get("url") or "").lower()
                       for bad in _NON_COMPANY)]
    return kept, len(hits) - len(kept)


# 설계 (2026-08 v3, 스카우트 궤적): 규칙 목록을 버리고 목적함수를 준다.
#
# v2까지의 실패 이력: 규칙을 쌓을수록 모델의 암묵적 결정 정책이 보수화되어
# "필드를 확신 있게 못 채우면 항목을 버린다"로 수렴했다(영문권 전멸,
# 반도체 배치 붕괴 실측). 근거 문헌이 같은 진단을 준다 — 모델은 보정된
# 확신 신호를 갖고 있으나 **자기 결정 정책이 그 신호를 쓰지 못한다**
# (arXiv 2601.07767). 처방: 모델은 항목별 확률만 산출하고(판정), 채택
# 여부는 코드가 기대효용 임계로 내린다(결정) — BAS(2604.03216)의
# answer-or-abstain 효용 모델, Search-R1(2503.09516)·ReAct의 관찰→가설→
# 판단 궤적을 결합한 구조다.
EXTRACT_SYSTEM = HARD_RULES + """

당신은 B2B 리드 스카우트다. 웹 검색 결과에서 [찾는 상대]에 부합할 수 있는
실존 기업을 발굴한다. 업종·지역·언어는 건마다 다르다 — 아무 업종도 가정하지
말고, 판단 근거는 [찾는 상대]와 각 항목의 자료뿐이다.

항목마다 같은 궤적을 독립적으로 밟는다:
  관찰 — 이 페이지의 주체는 누구인가. 기업 자신인가, 기업을 소개하는 제3자
        (기사·디렉터리·협회)인가, 기업과 무관한 글인가.
  가설 — 여기서 발굴할 수 있는 회사 이름은 무엇인가. 제목에 있든 본문에
        있든, 자료에 실제로 적힌 이름만 후보다.
  판정 — p = 그 이름의 회사가 실존하며 [찾는 상대]의 역할에 부합할 확률.
        확신이 아니라 **정직한 추정치**를 내라. 스니펫은 단편이므로 0.9를
        넘기 어렵고, 업이 맞아 보이면 0.5 아래로 내려갈 이유도 없다.
        채택·탈락은 네가 정하지 않는다 — p만 내면 시스템이 결정한다.
  출처 — 관찰 단계에서 본 것을 source_kind로 남긴다. 이 회사 이름이 나온
        페이지의 **주체**가 무엇인가:
          own       그 회사 자신의 사이트(회사소개·제품·채용 등 1인칭 페이지)
          directory 협회·회원사 목록·디렉터리·B2B 플랫폼처럼 회사를 소개하는
                    구조화된 3자 페이지
          mention   기사·블로그·비교글·채용공고 등에서 스쳐 언급된 경우
        판정 기준은 페이지의 화자다 — 도메인 이름으로 짐작하지 말고 내용으로
        본다. 이 값은 p와 별개다(회사가 훌륭해도 mention이면 mention이다).

한 항목에서 회사가 여럿 발굴되면 각각 별도 행으로 낸다(디렉터리·비교 기사).
어느 항목이 판정하기 어려워도 다른 항목의 p에 영향을 주지 마라.

불변 조건 (이것만은 절대적이다):
- 이름·설명은 자료에 있는 것만. 자료에 없는 회사를 만들어내면 p가 무의미해진다.
- name은 자료 표기 그대로(원어 유지 — 번역하면 검색·연락 때 못 찾는다).
- url은 그 회사가 나온 입력 항목의 URL 그대로.
- what은 자료가 말하는 만큼만 한 문장. signal은 관측된 신호가 있을 때만,
  없으면 빈 문자열."""

EXTRACT_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["companies"],
    "properties": {
        "companies": {
            "type": "array", "maxItems": 30,
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["name", "what", "signal", "url", "p", "source_kind"],
                "properties": {
                    "name": {"type": "string"},
                    "what": {"type": "string"},
                    "signal": {"type": "string"},
                    "url": {"type": "string"},
                    "p": {"type": "number", "minimum": 0, "maximum": 1},
                    "source_kind": {"type": "string",
                                    "enum": ["own", "directory", "mention"]},
                },
            },
        },
    },
}


def extract_companies(extractor, hits: list[dict], counterpart: str,
                      requester_name: str = "", _split: bool = True) -> list[dict]:
    """검색 히트 → 실존 기업 목록. 기본 LLM 1회.

    배치 붕괴 가드: 히트가 3건 이상인데 0곳이 나오면 반으로 갈라 한 번씩
    재시도한다. 실측(반도체 건): 판정이 어려운 항목 하나가 배치 전체를 빈
    배열로 무너뜨렸다 — 단독으로는 잡히던 기업이 그 항목과 같이 있으면
    사라진다. 프롬프트로 독립 판정을 지시해도 완전히 못 막으므로, 독립성은
    코드가 강제한다(판정은 모델, 결정은 코드). 최악 +2콜이지만 0곳으로
    끝나는 요청 자체가 드물어야 정상이다.
    """
    if not hits:
        return []
    listing = "\n".join(
        f"[{i + 1}] {h.get('title', '')}\n    URL: {h.get('url', '')}\n"
        f"    내용: {(h.get('snippet') or '')[:300]}"
        for i, h in enumerate(hits))
    data = extractor.extract_json(
        EXTRACT_SYSTEM,
        f"[요청 기업] {requester_name or '미상'}\n"
        f"[찾는 상대]\n{counterpart}\n\n[검색 결과]\n{listing}",
        EXTRACT_SCHEMA, deep=False, allow_foreign=True)
    seen, out = set(), []
    valid_urls = {h.get("url") for h in hits}
    tau = _accept_tau()
    dropped_low_p = 0
    for c in data.get("companies", []):
        name = (c.get("name") or "").strip()
        url = (c.get("url") or "").strip()
        # 계약 검증 — 입력에 없던 URL은 환각이다 (인용 계약과 같은 원리)
        if not name or name in seen or url not in valid_urls:
            continue
        # 기대효용 결정 — 유효 확률 p_eff = p·w(source)가 임계 미만이면 탈락.
        # p 누락은 0.5로 본다(중립) — 스키마가 강제하므로 방어적 기본값일 뿐.
        kind = (c.get("source_kind") or "").strip().lower()
        p_raw = float(c.get("p", 0.5))
        p_eff = round(p_raw * _source_weight(kind), 3)
        if p_eff < tau:
            dropped_low_p += 1
            continue
        seen.add(name)
        what = (c.get("what") or "").strip()
        sig = (c.get("signal") or "").strip()
        # 신호가 설명을 되풀이하면 버린다 — 화면에 같은 문장이 두 번 뜨고
        # 검색 점수도 중복 가중돼 왜곡된다(실측: 같은 문장 3회 반복).
        if sig and (sig in what or what in sig):
            sig = ""
        # name_ko(한국어 표기)는 추출에서 만들지 않는다 — 번역·독음은 별도의
        # 앱 계층 관심사다. 추출 프롬프트에 섞으면 "표기를 못 만들겠다"가
        # 기업 폐기로 이어지는 결합이 생긴다(실측: 영문권 전멸 사고의 일부).
        out.append({"name": name, "name_ko": name, "what": what,
                    "signal": sig, "url": url,
                    "p": p_eff, "p_raw": round(p_raw, 2), "source_kind": kind or "unknown"})
    if dropped_low_p:
        from .. import progress
        progress.log("검색", f"저확률 후보 {dropped_low_p}건 탈락 (p < {tau})")
    if not out and _split and len(hits) >= 3:
        from .. import progress
        progress.log("검색", f"⚠ 추출 0곳(히트 {len(hits)}건) — 배치를 갈라 재시도")
        mid = len(hits) // 2
        seen2, merged = set(), []
        for half in (hits[:mid], hits[mid:]):
            for c in extract_companies(extractor, half, counterpart,
                                       requester_name, _split=False):
                if c["name"] not in seen2:
                    seen2.add(c["name"])
                    merged.append(c)
        return merged
    return out
