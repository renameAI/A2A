"""합성 관련도 페어 생성 (순수 파이썬 — API 불필요, 결정적).

목적: 실제 데이터(4000사 리서치 + Claude 스코어링, AXR 협의 gate) 전에 학습 스택
전체(특수토큰·unfreeze·LoRA·기댓값 readout)를 실제 GPU에서 검증하려면 '학습 가능한
구조'를 가진 합성 데이터가 필요하다. 랜덤 점수는 학습이 안 되므로, 도메인 보완성
그래프에서 점수를 결정적으로 유도한다 — 스코어러가 '무언가를 배웠는지'를 판별 가능.

이건 실제 데이터의 대체물이 아니라 파이프라인 검증용이다. 실데이터가 오면 같은
RelatednessPair 스키마로 교체된다 (app/augment.py의 합성과 같은 철학).
"""
import random

from .data import RelatednessPair

# 도메인과 그 '수요/공급 보완' 그래프 — 화살표는 "A의 산출물을 B가 필요로 함".
# 같은 노드=동종(경쟁, 낮음), 보완 엣지=높음, 무관=낮음. 학습 가능한 신호.
_DOMAINS = {
    "정밀감속기": {"role": "부품공급", "text": "정밀 유성·사이클로이드 감속기와 서보 액추에이터를 제조하는 모션컨트롤 부품사"},
    "로봇제조": {"role": "완제품", "text": "협동로봇과 산업용 매니퓰레이터를 설계·조립하는 로봇 완제품 제조사"},
    "자동화장비": {"role": "완제품", "text": "공장 자동화 라인과 물류 장비를 통합 구축하는 자동화 시스템 업체"},
    "배터리소재": {"role": "소재공급", "text": "이차전지 양극재와 바인더 소재를 개발·양산하는 배터리 소재 기업"},
    "전기차부품": {"role": "완제품", "text": "전기차 구동 모듈과 배터리 팩을 제조하는 전동화 부품사"},
    "센서반도체": {"role": "부품공급", "text": "산업용 비전 센서와 근접 센서 반도체를 설계하는 센서 전문 팹리스"},
    "물류로봇": {"role": "완제품", "text": "창고 자율주행 로봇(AMR)과 분류 시스템을 공급하는 물류 자동화 기업"},
    "식품소재": {"role": "소재공급", "text": "기능성 식품 원료와 대체 단백 소재를 생산하는 식품 소재 B2B"},
    "식품제조": {"role": "완제품", "text": "가공식품과 건강기능식품 완제품을 제조·유통하는 식품 기업"},
    "산업SW": {"role": "소프트웨어", "text": "제조 현장의 MES·설비 예지보전 소프트웨어를 공급하는 산업 SW 기업"},
}
# 보완 엣지 (공급→수요) — 점수 상한 8~10
_COMPLEMENT = {
    ("정밀감속기", "로봇제조"), ("정밀감속기", "자동화장비"), ("정밀감속기", "물류로봇"),
    ("센서반도체", "로봇제조"), ("센서반도체", "자동화장비"), ("센서반도체", "물류로봇"),
    ("배터리소재", "전기차부품"), ("식품소재", "식품제조"),
    ("산업SW", "로봇제조"), ("산업SW", "자동화장비"), ("산업SW", "전기차부품"),
    ("로봇제조", "물류로봇"),   # 로봇 → 물류 통합
}
_REGIONS = ["한국", "대만", "일본", "베트남", "독일", "미국"]
_NAME_A = ["한올", "성진", "다온", "누리", "가온", "새빛", "이든", "라온", "해솔", "정우",
           "대륙", "동방", "極東", "北方", "泰山"]   # 다국어 섞음(현실 반영)
_NAME_B = ["테크", "정밀", "시스템", "소재", "로보틱스", "머티리얼즈", "일렉트릭", "工業", "精機"]


def _complement_score(da: str, db: str, rng: random.Random) -> int:
    """도메인 쌍 → 0~10 관련도 (결정적 구조 + 소량 노이즈)."""
    if da == db:
        base = 3           # 동종 = 경쟁, 보완 낮음
    elif (da, db) in _COMPLEMENT or (db, da) in _COMPLEMENT:
        base = 9           # 직접 보완
    else:
        # 2홉 보완(A→X, X→B 같은 매개)이 있으면 중간
        mid = any((da, x) in _COMPLEMENT or (x, da) in _COMPLEMENT
                  for x in _DOMAINS
                  if (x, db) in _COMPLEMENT or (db, x) in _COMPLEMENT)
        base = 5 if mid else 1
    return max(0, min(10, base + rng.choice([-1, 0, 0, 1])))


def _company(rng: random.Random, idx: int) -> tuple:
    domain = rng.choice(list(_DOMAINS))
    region = rng.choice(_REGIONS)
    name = f"{rng.choice(_NAME_A)}{rng.choice(_NAME_B)}-{idx}"
    text = f"{region}에 본사를 둔 {_DOMAINS[domain]['text']}. 주력 분야는 {domain}이다."
    return name, domain, text


def generate(n_companies: int = 300, n_pairs: int = 4000, seed: int = 42,
             mode: str = "research") -> list:
    """회사 n개 생성 → 페어 n_pairs개를 도메인 보완 구조로 점수화. 결정적."""
    rng = random.Random(seed)
    companies = [_company(rng, i) for i in range(n_companies)]
    pairs, seen = [], set()
    attempts = 0
    while len(pairs) < n_pairs and attempts < n_pairs * 8:
        attempts += 1
        (na, da, ta), (nb, db, tb) = rng.sample(companies, 2)
        key = tuple(sorted([na, nb]))
        if key in seen:
            continue
        seen.add(key)
        score = _complement_score(da, db, rng)
        pairs.append(RelatednessPair(
            a_id=na, a_text=ta, b_id=nb, b_text=tb, score=score,
            mode=mode, source="synthetic-structured"))
    return pairs


def main() -> None:
    import argparse
    import json
    from pathlib import Path
    ap = argparse.ArgumentParser(
        description="합성 관련도 페어 생성 (파이프라인 검증용 — API 불필요)")
    ap.add_argument("--out", default="dataset/scorer_pairs_synth.jsonl")
    ap.add_argument("--companies", type=int, default=300)
    ap.add_argument("--pairs", type=int, default=4000)
    ap.add_argument("--seed", type=int, default=42)
    a = ap.parse_args()
    pairs = generate(a.companies, a.pairs, a.seed)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    from dataclasses import asdict
    with open(out, "w", encoding="utf-8") as f:
        for p in pairs:
            f.write(json.dumps(asdict(p), ensure_ascii=False) + "\n")
    from collections import Counter
    hist = Counter(p.score for p in pairs)
    print(f"합성 페어 {len(pairs)}건 → {out}")
    print("점수 분포:", dict(sorted(hist.items())))


if __name__ == "__main__":
    main()
