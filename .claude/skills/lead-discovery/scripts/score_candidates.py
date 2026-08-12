"""Claude 스킬용 후보 채점기 — LLM 없이 엔진의 결정적 경로만 실행.

역할 분담(엔진 철학 그대로): 상대상 합성은 Claude(스킬을 실행하는 모델)가 하고,
점수·임계·경쟁사 강등·의도 티어는 이 스크립트가 엔진 코드를 재사용해 계산한다.
엔진의 synthesize_counterpart(LLM 호출)를 우회하는 대신 --synth로 주입받는다.

사용:
  .venv/bin/python .claude/skills/lead-discovery/scripts/score_candidates.py \
      --profile /tmp/profile.json --synth /tmp/synth.txt \
      --region 베트남 --vps revenue_growth --k 5
"""
import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]   # …/scripts → lead-discovery → skills → .claude → 저장소 루트
sys.path.insert(0, str(ROOT))
os.environ.setdefault("A2A_POOL_DIR", str(ROOT / "judge_cases" / "demo_hotels_pool"))
os.environ.setdefault("A2A_SEED_POOL", "0")

from app.engine.pool import CandidateRecord, get_pool, _inferred, _stated  # noqa: E402
from app.engine.retrieve import (_STRONG_THRESHOLD, _intent_tier,  # noqa: E402
                                 _match_points, _score, _search_text,
                                 template_counterpart)
from app.schemas import (BasicInfo, Intent, PoolChoice, PoolKind,  # noqa: E402
                         Profile, RetrieveDirection, RetrieveRequest)


def _records_from_file(path: str) -> "list[CandidateRecord]":
    """웹에서 모은 후보 JSON → CandidateRecord (gstack형 배포 모드 — 풀 불필요).

    입력: [{"name","country","industry","description","pain_signal","solution?","url?"}]
    Claude가 WebSearch로 모아 쓴 파일. 실재 근거(pain_signal)는 반드시 원문에서
    관측된 것만 담는다는 계약은 SKILL.md가 강제한다.
    """
    rows = json.loads(Path(path).read_text(encoding="utf-8"), strict=False)
    out = []
    for i, r in enumerate(rows):
        name = r.get("name") or f"후보{i+1}"
        pain = (r.get("pain_signal") or "").strip()
        desc = (r.get("description") or "").strip()
        profile = Profile(
            basic=BasicInfo(name=name, country=r.get("country") or "미상",
                            city=None, founded_year=None,
                            industry=r.get("industry") or "unknown"),
            description=desc or pain or name,
            problem_solved=_inferred(pain, 0.8) if pain else _inferred(desc, 0.5),
            solution=_stated(r.get("solution") or "") if r.get("solution")
                     else _inferred("미상", 0.3),
            target_customer=_inferred("미상", 0.3),
            references=[], traction=None,
            sell_value_props=[], purchase_value_props=[],
            willingness_sell=None, willingness_purchase=None)
        out.append(CandidateRecord(
            company_id=f"web-{i+1:02d}-{name}", pool=PoolKind.external,
            profile=profile, pain_points=f"{pain} {desc}".strip(),
            tags=[t for t in (r.get("industry"), r.get("country")) if t]))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", required=True, help="Profile JSON 파일 경로")
    ap.add_argument("--synth", required=True, help="Claude가 합성한 상대상 텍스트 파일")
    ap.add_argument("--region", default=None)
    ap.add_argument("--vps", default="revenue_growth",
                    help="쉼표 구분 value_props")
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--candidates", default=None,
                    help="웹 수집 후보 JSON 파일 — 주면 내부 풀 대신 이 후보만 채점")
    args = ap.parse_args()

    profile = Profile.model_validate(
        json.loads(Path(args.profile).read_text(encoding="utf-8"), strict=False))
    synth = Path(args.synth).read_text(encoding="utf-8").strip()
    req = RetrieveRequest(
        requester_profile=profile,
        intent=Intent(value_props=args.vps.split(","),
                      target_region=args.region),
        direction=RetrieveDirection.sell_outreach,
        pool=PoolChoice.both, k=args.k, allow_weak=True)

    anchor = template_counterpart(req)
    records = (_records_from_file(args.candidates) if args.candidates
               else list(get_pool()))
    records = [r for r in records
               if r.profile.basic.name != profile.basic.name]
    scored = sorted(
        ((r, _score(req, synth, anchor, r)) for r in records),
        key=lambda x: (tuple(-v for v in _intent_tier(req, x[0])),
                       -x[1], x[0].company_id))

    out = {
        "anchor": anchor,
        "threshold": _STRONG_THRESHOLD,
        "pool_size": len(records),
        "candidates": [
            {"rank": i + 1,
             "company_id": r.company_id,
             "name": r.profile.basic.name,
             "country": r.profile.basic.country,
             "score": s,
             "passes_threshold": s >= _STRONG_THRESHOLD,
             "intent_tier": list(_intent_tier(req, r)),
             "match_points": _match_points(synth, anchor, r),
             "summary": r.profile.description[:160]}
            for i, (r, s) in enumerate(scored[: args.k])
        ],
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
