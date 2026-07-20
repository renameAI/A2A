"""Phase 4 — CoT 데이터 파이프라인 (DAT-01~05).

감사 로그(audit/*.jsonl)에 쌓인 엔진 추론 궤적을 재학습용 데이터셋으로 만든다.
audit.record가 represent/judge/consult/negotiate를 append하고, 이 모듈이 그 위에
검증·커버리지·분할·봉인을 얹는다. 순수 데이터 엔지니어링 — LLM·네트워크 없음.

  validate_records()   DAT-01/02  스키마 검증 — kind별 필수 필드, 불량 라인 격리
  coverage_matrix()    DAT-03      커버리지 — kind × 차원 분포 (데이터 공백 진단)
  split_held_out()     DAT-04      결정적 분할 — 주체명 해시로 train/held-out 봉인
  seal() / verify()    DAT-05      봉인 — held-out 지문 + train 누수 검사

결정성: 분할은 hashlib(주체명) 기반이라 실행마다 동일하다. Date.now/random을 쓰지
않는다 — 같은 입력이면 같은 train/held-out이 나와야 재현·감사가 가능하다.
"""
import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

# kind별 필수 필드 (audit.record 페이로드와 일치) — 공통 {ts, kind}에 더해.
REQUIRED_FIELDS = {
    "represent": ["name", "engine_mode", "assets", "open_questions"],
    "judge": ["self", "counterpart", "vantage", "objective", "decision",
              "verdicts", "trajectory"],
    "consult": ["company", "turn", "history", "output"],
    "negotiate": ["seller", "buyer", "termination", "rounds_used", "rounds"],
    "scout": ["name", "engine_mode", "hypotheses", "shortlist"],
}

# kind별 '주체명' 필드 — 분할 해시 키. 같은 회사의 여러 궤적이 train/held-out에
# 갈라지지 않도록(누수 방지) 회사 단위로 묶는다.
SUBJECT_FIELD = {"represent": "name", "judge": "self",
                 "consult": "company", "negotiate": "seller", "scout": "name"}


@dataclass
class ValidationReport:
    valid: list[dict] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)   # {line, source, reason}

    @property
    def ok(self) -> bool:
        return not self.errors


def load_records(audit_dir: str | Path) -> list[dict]:
    """audit_dir의 모든 *.jsonl을 읽어 레코드 리스트로. 각 레코드에 _source(파일명)·
    _line(1-base) 메타를 붙인다. 파싱 불가 라인은 _malformed=True로 표시해 넘긴다."""
    directory = Path(audit_dir)
    records: list[dict] = []
    for path in sorted(directory.glob("*.jsonl")):
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                obj["_source"], obj["_line"] = path.name, i
                records.append(obj)
            except json.JSONDecodeError:
                records.append({"_malformed": True, "_source": path.name,
                                "_line": i, "_raw": line[:200]})
    return records


def validate_records(records: list[dict]) -> ValidationReport:
    """DAT-01/02 — 각 레코드가 kind별 필수 필드를 갖췄는지. 불량은 격리한다."""
    report = ValidationReport()
    for rec in records:
        loc = {"source": rec.get("_source"), "line": rec.get("_line")}
        if rec.get("_malformed"):
            report.errors.append({**loc, "reason": "JSON 파싱 실패"})
            continue
        kind = rec.get("kind")
        if kind not in REQUIRED_FIELDS:
            report.errors.append({**loc, "reason": f"미지 kind: {kind!r}"})
            continue
        missing = [f for f in REQUIRED_FIELDS[kind] if f not in rec]
        if missing:
            report.errors.append({**loc, "reason": f"{kind} 필수 필드 누락: {missing}"})
            continue
        report.valid.append(rec)
    return report


def coverage_matrix(records: list[dict]) -> dict:
    """DAT-03 — kind별 개수 + kind별 핵심 차원 분포. 데이터가 얇은 축을 드러낸다."""
    matrix: dict = {"total": len(records), "by_kind": {}, "dimensions": {}}
    dims = {
        "represent": ["engine_mode"],
        "judge": ["vantage", "objective", "decision"],
        "negotiate": ["termination"],
        "consult": [],
    }
    for rec in records:
        kind = rec.get("kind")
        matrix["by_kind"][kind] = matrix["by_kind"].get(kind, 0) + 1
        for dim in dims.get(kind, []):
            key = f"{kind}.{dim}"
            bucket = matrix["dimensions"].setdefault(key, {})
            val = str(rec.get(dim))
            bucket[val] = bucket.get(val, 0) + 1
    return matrix


def _subject_key(rec: dict) -> str:
    """분할 해시 키 — kind의 주체명. 없으면 source:line으로 폴백(레코드 단위 분할)."""
    field_name = SUBJECT_FIELD.get(rec.get("kind"))
    subject = rec.get(field_name) if field_name else None
    return str(subject) if subject else f"{rec.get('_source')}:{rec.get('_line')}"


def _bucket(key: str) -> int:
    """주체명 → [0,99] 결정적 버킷 (sha256, 실행·플랫폼 불변)."""
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % 100


def split_held_out(records: list[dict], held_frac: float = 0.2
                   ) -> tuple[list[dict], list[dict]]:
    """DAT-04 — 주체명 해시로 결정적 train/held-out 분할.

    같은 회사의 모든 궤적은 같은 버킷 → 한쪽에만 간다(누수 방지). 버킷 < held_frac*100
    이면 held-out. held_frac=0.2면 회사의 약 20%가 held-out으로 봉인된다."""
    threshold = round(held_frac * 100)
    train, held = [], []
    for rec in records:
        (held if _bucket(_subject_key(rec)) < threshold else train).append(rec)
    return train, held


def _fingerprint(rec: dict) -> str:
    """레코드 지문 — 메타(_source/_line 등) 제외 내용 해시. 봉인 무결성 검사용."""
    core = {k: v for k, v in rec.items() if not k.startswith("_")}
    return hashlib.sha256(
        json.dumps(core, ensure_ascii=False, sort_keys=True,
                   default=str).encode("utf-8")).hexdigest()


def seal(held: list[dict]) -> dict:
    """DAT-05 — held-out 봉인. 주체 목록 + 레코드 지문을 담은 매니페스트."""
    return {
        "sealed": True,
        "count": len(held),
        "subjects": sorted({_subject_key(r) for r in held}),
        "fingerprints": sorted(_fingerprint(r) for r in held),
    }


def verify_seal(train: list[dict], sealed: dict) -> list[str]:
    """DAT-05 — 봉인 검증. train에 held-out 주체·지문이 새어들었는지. 위반 목록 반환."""
    held_subjects = set(sealed.get("subjects", []))
    held_prints = set(sealed.get("fingerprints", []))
    violations = []
    for rec in train:
        if _subject_key(rec) in held_subjects:
            violations.append(f"주체 누수: {_subject_key(rec)} "
                              f"({rec.get('_source')}:{rec.get('_line')})")
        if _fingerprint(rec) in held_prints:
            violations.append(f"지문 누수: {rec.get('_source')}:{rec.get('_line')}")
    return violations


def build(audit_dir: str | Path, out_dir: str | Path,
          held_frac: float = 0.2) -> dict:
    """전체 파이프라인 — 로드→검증→커버리지→분할→봉인→검증. 요약 dict 반환."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    records = load_records(audit_dir)
    report = validate_records(records)
    coverage = coverage_matrix(report.valid)
    train, held = split_held_out(report.valid, held_frac)
    sealed = seal(held)
    violations = verify_seal(train, sealed)

    _write_jsonl(out / "train.jsonl", train)
    _write_jsonl(out / "heldout.jsonl", held)
    (out / "seal.json").write_text(
        json.dumps(sealed, ensure_ascii=False, indent=2), encoding="utf-8")

    # SFT 학습쌍 — 입력 캡처된 궤적만 변환된다 (구버전 로그는 skip 집계에 드러남)
    train_sft, train_skip = to_sft(train)
    held_sft, held_skip = to_sft(held)
    _write_jsonl(out / "train_sft.jsonl", train_sft)
    _write_jsonl(out / "heldout_sft.jsonl", held_sft)

    summary = {
        "total_lines": len(records),
        "valid": len(report.valid),
        "errors": len(report.errors),
        "train": len(train),
        "heldout": len(held),
        "train_sft": len(train_sft),
        "heldout_sft": len(held_sft),
        "sft_skipped": {"train": train_skip, "heldout": held_skip},
        "coverage": coverage,
        "seal_violations": violations,
    }
    (out / "report.json").write_text(
        json.dumps({**summary, "validation_errors": report.errors},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


# ── SFT 변환 — 궤적 → chat 학습쌍 ─────────────────────────────────
# 엔진이 실제로 쓰는 프롬프트(system+user → 구조화 JSON)와 1:1이어야
# 학습-추론 분포가 일치한다. 입력 원문(input_text)이 없는 레코드(입력 캡처
# 이전의 구버전 로그)는 학습쌍을 복원할 수 없으므로 정직하게 건너뛰고 센다.

def to_sft(records: list[dict]) -> tuple[list[dict], dict]:
    """검증된 audit 레코드 → chat-format SFT 예제. 반환: (examples, skip 집계).

    represent: input_text → EXTRACT_SYSTEM / 라벨 = profile_json + open_questions
    judge:     input_text(judge_user 전문) → JUDGE_SYSTEM / 라벨 = result_json
    consult/negotiate/scout: 입력 재구성이 아직 불완전 — v1 범위 밖 (집계에 표시).
    """
    from .engine.prompts import EXTRACT_SYSTEM, JUDGE_SYSTEM
    examples: list[dict] = []
    skipped = {"no_input": 0, "kind_out_of_scope": 0}
    for rec in records:
        kind = rec.get("kind")
        if kind == "represent":
            if not rec.get("input_text") or not rec.get("profile_json"):
                skipped["no_input"] += 1
                continue
            label = {"profile": rec["profile_json"],
                     "open_questions": rec.get("open_questions", [])}
            examples.append({
                "messages": [
                    {"role": "system", "content": EXTRACT_SYSTEM},
                    {"role": "user", "content": rec["input_text"]},
                    {"role": "assistant",
                     "content": json.dumps(label, ensure_ascii=False)},
                ],
                "meta": {"kind": kind, "subject": rec.get("name"),
                         "engine_mode": rec.get("engine_mode"),
                         "structured_input": True,
                         "label_source": "trajectory"},
            })
        elif kind == "judge":
            if not rec.get("input_text") or not rec.get("result_json"):
                skipped["no_input"] += 1
                continue
            examples.append({
                "messages": [
                    {"role": "system", "content": JUDGE_SYSTEM},
                    {"role": "user", "content": rec["input_text"]},
                    {"role": "assistant",
                     "content": json.dumps(rec["result_json"],
                                           ensure_ascii=False)},
                ],
                "meta": {"kind": kind, "subject": rec.get("self"),
                         "structured_input": False,
                         "label_source": "trajectory"},
            })
        else:
            skipped["kind_out_of_scope"] += 1
    return examples, skipped


def _write_jsonl(path: Path, records: list[dict]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for rec in records:
            core = {k: v for k, v in rec.items() if not k.startswith("_")}
            f.write(json.dumps(core, ensure_ascii=False, default=str) + "\n")


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description="Phase 4 CoT 데이터 파이프라인 (DAT-01~05)")
    ap.add_argument("--audit-dir",
                    default=os.environ.get("A2A_AUDIT_DIR", "audit"))
    ap.add_argument("--out-dir", default="dataset")
    ap.add_argument("--held-frac", type=float, default=0.2)
    args = ap.parse_args()
    summary = build(args.audit_dir, args.out_dir, args.held_frac)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if summary["seal_violations"]:
        raise SystemExit("❌ 봉인 위반 — held-out이 train에 누수됨 (위 목록 참고)")
    print(f"\n✅ {args.out_dir}/ 에 train({summary['train']})·"
          f"heldout({summary['heldout']})·seal·report 생성")


if __name__ == "__main__":
    main()
