"""키워드 원장과 공동출현 추천 — "비슷한 키워드로 찾은 건은 어떤 키워드를 썼나".

왜 필요한가: 검색어 생성은 매 요청마다 LLM이 백지에서 만든다. 그래서 같은 시장을
두 번째로 파는 사람이 첫 번째 사람의 시행착오를 물려받지 못한다 — 어떤 키워드가
실제로 기업을 물어왔고 어떤 키워드가 헛돌았는지가 매번 버려진다.

이 모듈은 그 기록을 남기고 되돌려준다. 요청이 끝날 때 (키워드 → 그 키워드로 실제
추출된 기업 수)를 원장에 적고, 새 요청이 들어오면 **키워드가 겹치는 과거 요청들**을
찾아 그들이 함께 썼던 다른 키워드를 추천한다. 고전적인 item-based 공동출현이다.

의도적으로 하지 않은 것:
- 임베딩 유사도를 쓰지 않는다. 키워드는 짧고 고유명사가 많아 토큰 일치가 더 정확하고,
  무엇보다 "왜 추천됐는가"를 사용자에게 문자 그대로 보여줄 수 있다.
- 실적이 없으면 추천하지 않는다. 콜드스타트에 그럴듯한 키워드를 지어내면 그건
  추천이 아니라 또 하나의 추측이다 — 빈 목록을 반환하고 UI가 그 사실을 말한다.
"""
import re
from collections import defaultdict

# 검색어를 토큰으로 쪼갠다. CJK는 공백이 없으므로 2-gram으로, 그 외는 단어 단위로.
_WORD = re.compile(r"[A-Za-z0-9가-힣]+|[぀-ヿ一-鿿]+")
_CJK = re.compile(r"^[぀-ヿ一-鿿]+$")
# 어느 업종에서나 나오는 말 — 겹쳐도 '비슷한 검색'이라는 신호가 못 된다
_STOP = {"회사소개", "기업정보", "공식", "사이트", "사업영역", "company", "profile",
         "official", "about", "会社概要", "企業情報", "公司簡介", "list", "명단"}


def tokenize(query: str) -> set[str]:
    """검색어 → 비교 가능한 토큰 집합."""
    out: set[str] = set()
    for w in _WORD.findall(query):
        if w.lower() in _STOP or len(w) < 2:
            continue
        if _CJK.match(w):
            # CJK는 형태소 분석기 없이 2-gram으로 자른다 — '食品卸売業者'가
            # '食品','品卸','卸売','売業','業者'로 쪼개져 '食品卸'와 겹친다
            out.update(w[i:i + 2] for i in range(len(w) - 1))
            if len(w) <= 4:
                out.add(w)
        else:
            out.add(w.lower())
    return out


def axis_tokens(ontologies: list) -> set[str]:
    """온톨로지 축의 값에서 비교 토큰을 뽑는다.

    문자열 원문(회사명·검색어)이 아니라 **판독된 축**을 비교면으로 쓴다. 검색어는
    표현이 흔들리지만(같은 회사를 '食品卸'로도 '食品流通'으로도 찾는다) 축은
    "옮기는 쪽에 서 있고, 식품을 들여와 소매에 넘긴다"로 수렴한다. unknown 축은
    비교에 넣지 않는다 — 모른다는 사실이 비슷함의 근거가 될 수는 없다.
    """
    out: set[str] = set()
    for ont in ontologies:
        axes = ont.get("axes", {}) if isinstance(ont, dict) else ont.axes
        for a in axes.values():
            val = a.get("value", "") if isinstance(a, dict) else a.value
            st = a.get("status", "") if isinstance(a, dict) else a.status.value
            if st == "unknown" or not val:
                continue
            out |= tokenize(val)
    return out


def record_run(store, workspace_id: str, rid: str, *,
               segment: str, queries: list[str], yield_by_query: dict[str, int],
               kept: int, ontologies: list | None = None) -> None:
    """이번 검색이 무엇을 썼고 무엇을 건졌는지 원장에 남긴다.

    yield_by_query는 '그 검색어가 데려온 히트 중 실존 기업으로 살아남은 수'다.
    히트 수가 아니라 **살아남은 수**를 적는 이유: 블로그를 10건 물어온 검색어는
    성과가 0이지 10이 아니다.
    """
    entry = {
        "request_id": rid,
        "segment": segment,
        "queries": list(queries),
        "yield_by_query": {q: int(n) for q, n in yield_by_query.items()},
        "companies_kept": int(kept),
        # 검색어 토큰은 폴백이다 — 온톨로지가 있으면 축 토큰이 비교면이 된다
        "tokens": sorted({t for q in queries for t in tokenize(q)}),
        "axis_tokens": sorted(axis_tokens(ontologies or [])),
        # 온톨로지가 파생한 검색어 — 다음 검색의 확장 후보
        "derived_keywords": sorted({
            k for o in (ontologies or [])
            for k in ((o.get("search_keywords", []) if isinstance(o, dict)
                       else o.search_keywords) or [])}),
    }
    store.put("keyword_run", workspace_id, rid + "::" + (segment or "_"), entry)


# 결과 신뢰도 계층 — Hu-Koren-Volinsky(2008)의 c=1+αr 등급 가중을 이산화한 것.
# 암묵 신호는 선호가 아니라 신뢰도다: 발견(추출 생존)=기본 1, 저장=사용자의
# 관련성 판단(+2), 초안=노력 투자(+4), 답장=시장의 확인(+8 — 유일한 외부 검증).
# '답장 없음'은 감점하지 않는다 — 지연 피드백(아직 안 온 것)과 부정을 구분할
# 수 없고, HKV의 원칙(미관측≠부정)이 여기서도 성립한다.
OUTCOME_WEIGHTS = {"saved": 2.0, "drafted": 4.0, "replied": 8.0}


def outcome_weight(o: dict) -> float:
    """결과 한 건의 추가 신뢰도. 계층은 누적이다 — 답장까지 갔으면
    저장·초안도 거쳤다는 뜻이므로 합산이 곧 단조성이다."""
    w = 0.0
    if o.get("saved"):
        w += OUTCOME_WEIGHTS["saved"]
    if o.get("drafted"):
        w += OUTCOME_WEIGHTS["drafted"]
    if o.get("replied") == "yes":
        w += OUTCOME_WEIGHTS["replied"]
    return w


def recommend(store, workspace_id: str, current_queries: list[str], *,
              current_ontologies: list | None = None,
              exclude_rid: str = "", limit: int = 5) -> list[dict]:
    """키워드가 겹치는 과거 요청에서, 이번에 안 쓴 성과 있는 검색어를 추천한다.

    반환 항목의 why는 UI에 그대로 보여줄 근거다 — 추천의 이유를 감추지 않는다.
    결과 원장(outcome)이 있으면 '많이 찾힌 검색어'가 아니라 '실제로 통한
    검색어'가 위로 온다 — 저장·초안·답장이 그 검색어의 신뢰도를 올린다.
    """
    runs = [r for r in store.list("keyword_run", workspace_id)
            if not r.get("request_id", "").startswith(exclude_rid or "\0")]
    if not runs:
        return []
    # 검색어 → 그 검색어가 찾은 회사들의 결과 신뢰도 합 + 답장 수(why 표기용)
    ow: dict[str, float] = defaultdict(float)
    replies: dict[str, int] = defaultdict(int)
    drafts: dict[str, int] = defaultdict(int)
    for o in store.list("outcome", workspace_id):
        if o.get("request_id", "").startswith(exclude_rid or "\0"):
            continue
        q = (o.get("found_by") or "").strip()
        if not q:
            continue
        ow[q] += outcome_weight(o)
        if o.get("replied") == "yes":
            replies[q] += 1
        if o.get("drafted"):
            drafts[q] += 1

    # 비교면 선택 — 온톨로지가 있으면 축을, 없으면(첫 검색 전) 검색어를 쓴다
    cur_ax = axis_tokens(current_ontologies or [])
    use_axes = bool(cur_ax)
    cur = cur_ax if use_axes else {t for q in current_queries for t in tokenize(q)}
    if not cur:
        return []
    cur_q = {q.strip() for q in current_queries}

    # 후보 검색어 점수 = Σ (겹침도 × 그 검색어의 실제 성과)
    scored: dict[str, float] = defaultdict(float)
    reasons: dict[str, list[str]] = defaultdict(list)
    for run in runs:
        toks = set(run.get("axis_tokens" if use_axes else "tokens", []))
        if not toks:
            continue
        # Jaccard — 긴 검색어가 겹침 수만으로 유리해지는 것을 막는다
        sim = len(cur & toks) / len(cur | toks)
        if sim < 0.05:
            continue
        for q, n in (run.get("yield_by_query") or {}).items():
            if n <= 0 or q.strip() in cur_q:
                continue     # 성과 0이거나 이미 쓰는 검색어는 추천하지 않는다
            scored[q] += sim * (n + ow.get(q, 0.0))
            why = f"{run.get('segment') or '이전 검색'}에서 {n}곳"
            if replies.get(q):
                why += f" · 답장 {replies[q]}건"
            elif drafts.get(q):
                why += f" · 초안 {drafts[q]}건"
            reasons[q].append(why)
        # 그 건의 기업들이 스스로 파생한 검색어 — 실적 대신 그 건의 수확량을 쓴다
        kept = run.get("companies_kept", 0)
        for q in run.get("derived_keywords", []):
            if not q.strip() or q.strip() in cur_q or kept <= 0:
                continue
            scored[q] += sim * kept * 0.5   # 실측 실적이 아니므로 절반만 신뢰
            reasons[q].append(f"{run.get('segment') or '이전 검색'} 기업들의 판독에서 파생")

    out = []
    for q, sc in sorted(scored.items(), key=lambda kv: -kv[1])[:limit]:
        out.append({"query": q, "score": round(sc, 3),
                    "why": " · ".join(reasons[q][:2])})
    return out
