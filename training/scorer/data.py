"""관련도 페어 데이터 — 스키마·검증·계층 샘플링·회사 단위 분할 (순수 파이썬).

정직성 원칙(app/dataset.py와 동일 계보): 결정적(같은 seed=같은 출력), 회사 단위
누수 차단(한 회사가 train/held 양쪽에 못 감), 라벨 품질을 정직하게 집계한다.

★ 계층 샘플링이 이 파이프라인의 성패를 가른다:
  4000개 기업 → 약 800만 쌍. 랜덤이면 99%가 0~2점(무관)이라 모델이 '무조건 낮게'
  찍고도 정확해 보인다. 점수 버킷별로 상한을 걸어 균형을 맞춘 뒤 학습해야 한다.
"""
import hashlib
import json
import random
from dataclasses import asdict, dataclass
from pathlib import Path

SCORE_MIN, SCORE_MAX = 0, 10
MODES = ("research", "ontology")   # 리서치 결과 / represent 온톨로지


@dataclass
class RelatednessPair:
    a_id: str
    a_text: str
    b_id: str
    b_text: str
    score: int                    # 0~10 (Claude가 매긴 관련도)
    mode: str = "research"        # research | ontology
    source: str = ""              # 라벨 출처(예: "claude-opus-4.8")

    def key(self) -> str:
        # 무방향 쌍 키 — (A,B)와 (B,A)를 같은 쌍으로 (대칭 점수 전제)
        lo, hi = sorted([self.a_id, self.b_id])
        return f"{lo}|{hi}|{self.mode}"


def load_pairs(path) -> list:
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        d = json.loads(line)
        out.append(RelatednessPair(**{k: d[k] for k in
                    ("a_id", "a_text", "b_id", "b_text", "score")
                    if k in d}, mode=d.get("mode", "research"),
                    source=d.get("source", "")))
    return out


def validate(pairs) -> dict:
    """불량 격리 + 정직 집계. 반환: {valid, errors:[{i,reason}]}."""
    valid, errors = [], []
    seen = set()
    for i, p in enumerate(pairs):
        if not isinstance(p.score, int) or not (SCORE_MIN <= p.score <= SCORE_MAX):
            errors.append({"i": i, "reason": f"점수 범위 밖: {p.score!r}"})
            continue
        if p.mode not in MODES:
            errors.append({"i": i, "reason": f"미지 mode: {p.mode!r}"})
            continue
        if not p.a_text.strip() or not p.b_text.strip():
            errors.append({"i": i, "reason": "빈 리서치 텍스트"})
            continue
        if p.a_id == p.b_id:
            errors.append({"i": i, "reason": "자기 자신과의 쌍"})
            continue
        k = p.key()
        if k in seen:
            errors.append({"i": i, "reason": f"중복 쌍: {k}"})
            continue
        seen.add(k)
        valid.append(p)
    return {"valid": valid, "errors": errors}


def histogram(pairs) -> dict:
    """점수 버킷별 개수 — 불균형을 눈으로 본다."""
    h = {s: 0 for s in range(SCORE_MIN, SCORE_MAX + 1)}
    for p in pairs:
        h[p.score] = h.get(p.score, 0) + 1
    return h


def stratified_sample(pairs, per_bucket_cap: int, seed: int = 42) -> tuple:
    """점수 버킷별 상한 샘플링. 반환: (표본, 리포트).

    각 버킷을 seed로 결정적 셔플 후 cap개까지. 버킷이 cap보다 작으면 전부 유지.
    이것이 '99% 0점' 붕괴를 막는 핵심 장치다."""
    rng = random.Random(seed)
    by_bucket = {s: [] for s in range(SCORE_MIN, SCORE_MAX + 1)}
    for p in pairs:
        by_bucket[p.score].append(p)
    sampled, before, after = [], {}, {}
    for s in range(SCORE_MIN, SCORE_MAX + 1):
        bucket = by_bucket[s]
        before[s] = len(bucket)
        rng.shuffle(bucket)
        keep = bucket[:per_bucket_cap]
        after[s] = len(keep)
        sampled.extend(keep)
    rng.shuffle(sampled)   # 버킷 순서 뭉침 제거
    return sampled, {"before": before, "after": after, "total": len(sampled)}


def _bucket(company_id: str) -> int:
    return int(hashlib.sha256(company_id.encode("utf-8")).hexdigest()[:8], 16) % 100


def split_by_company(pairs, held_frac: float = 0.15, seed: int = 42) -> tuple:
    """회사 단위 train/held 분할 (누수 0). 반환: (train, held, dropped).

    회사를 해시로 train/held 버킷에 배정하고:
      · 양쪽 회사 모두 train 버킷 → train
      · 양쪽 회사 모두 held 버킷  → held (진짜 '처음 보는 회사' 쌍 = OOD 평가)
      · 한쪽만 held(교차 쌍)      → 폐기. 어느 쪽에 넣어도 held 회사가 새어
        train과 겹치므로, 깨끗한 분할을 위해 버린다(폐기 수는 리포트).
    이렇게 해야 held-out이 회사명 암기가 아닌 판단 구조 전이를 측정한다.
    seed는 예약 인자(해시는 회사명 기반이라 결정적)."""
    threshold = round(held_frac * 100)
    is_held = lambda cid: _bucket(cid) < threshold
    train, held, dropped = [], [], []
    for p in pairs:
        ah, bh = is_held(p.a_id), is_held(p.b_id)
        if not ah and not bh:
            train.append(p)
        elif ah and bh:
            held.append(p)
        else:
            dropped.append(p)      # 교차 쌍 — 누수 방지 위해 폐기
    return train, held, dropped


def to_jsonl(pairs, path) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for p in pairs:
            f.write(json.dumps(asdict(p), ensure_ascii=False) + "\n")
