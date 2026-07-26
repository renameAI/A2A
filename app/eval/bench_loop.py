"""전체 loop 성능 벤치 — 단계별 wall-clock · LLM 콜 수 · 품질을 함께 잰다.

목적(② 계측 하네스): "현재 loop가 몇 초냐"를 숫자로 못박아, 이후 최적화의
개선치를 정직하게 측정한다. 지연만 재면 품질 저해를 놓치므로 두 축을 같이 낸다:
  · 지연: represent/retrieve/judge 단계별 p50·p90 초 + LLM 호출 수
  · 품질: 기존 골든 채점(retrieve top-1, judge 결정 극성) 재사용

provider 무관(mock/실LLM 모두). 실LLM은 judge가 후보당 130s+라 느리다 —
--judge-topk로 캐스케이드(상위 N만 판단)를 시뮬레이션해 예산을 통제한다.

실행:
  .venv/bin/python -m app.eval.bench_loop --stages retrieve,judge --repeat 1
  .venv/bin/python -m app.eval.bench_loop --stages retrieve --repeat 3 --json out.json
"""
import argparse
import json
import time
from statistics import median

from .. import progress
from .benchmark import (_run_judge_once, _run_retrieve_once, _score_judge,
                        _score_retrieve, load_golden)


def _p90(xs: list[float]) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    return round(s[min(len(s) - 1, int(round(0.9 * (len(s) - 1))))], 1)


def _timed(fn):
    """fn을 progress 컨텍스트에 바인딩해 실행 — (결과, wall_s, stage_timings, llm_calls)."""
    run = progress.bind()
    t0 = time.time()
    result = fn()
    wall = round(time.time() - t0, 1)
    return result, wall, run.stage_timings(), run.llm_calls


def _agg(samples: list[dict], key: str) -> dict:
    xs = [s[key] for s in samples]
    return {"p50": round(median(xs), 1) if xs else 0.0, "p90": _p90(xs),
            "max": round(max(xs), 1) if xs else 0.0, "n": len(xs)}


def bench(stages: list[str], repeat: int, judge_topk: int) -> dict:
    golden = load_golden()
    report: dict = {"stages": stages, "repeat": repeat, "judge_topk": judge_topk}

    if "retrieve" in stages:
        samples = []
        for case in golden.get("retrieve_cases", []):
            for _ in range(repeat):
                ranking, wall, timings, calls = _timed(lambda: _run_retrieve_once(case))
                q = _score_retrieve([ranking], case["expected"])
                samples.append({"case_id": case["case_id"], "wall_s": wall,
                                "llm_calls": calls, "top1_hit": q["top1_accuracy"],
                                "timings": timings})
        report["retrieve"] = {
            "wall_s": _agg(samples, "wall_s"),
            "llm_calls": _agg(samples, "llm_calls"),
            "top1_accuracy": round(sum(s["top1_hit"] for s in samples) / len(samples), 4) if samples else 0,
            "per_case": samples,
        }

    if "judge" in stages:
        samples = []
        cases = golden.get("judge_cases", [])[:judge_topk] if judge_topk else golden.get("judge_cases", [])
        for case in cases:
            for _ in range(repeat):
                res, wall, timings, calls = _timed(lambda: _run_judge_once(case))
                q = _score_judge([res], case["expected"])
                samples.append({"case_id": case["case_id"], "wall_s": wall,
                                "llm_calls": calls, "accuracy": q["accuracy"],
                                "timings": timings})
        report["judge"] = {
            "wall_s": _agg(samples, "wall_s"),
            "llm_calls": _agg(samples, "llm_calls"),
            "decision_accuracy": round(sum(s["accuracy"] for s in samples) / len(samples), 4) if samples else 0,
            "per_case": samples,
        }

    # 전체 loop 예산 시뮬 — represent(1콜×2) + retrieve(1) + judge(topk 후보 순차)
    r_wall = report.get("retrieve", {}).get("wall_s", {}).get("p90", 0.0)
    j_wall = report.get("judge", {}).get("wall_s", {}).get("p90", 0.0)
    n_judge = judge_topk or len(golden.get("judge_cases", []))
    report["loop_budget_estimate"] = {
        "note": "represent는 별도 측정 필요(온보딩 자료). 여기선 retrieve + judge×N 순차 합만.",
        "retrieve_p90_s": r_wall,
        "judge_p90_per_candidate_s": j_wall,
        "judge_candidates": n_judge,
        "judge_sequential_s": round(j_wall * n_judge, 1),
        "retrieve_plus_judge_sequential_s": round(r_wall + j_wall * n_judge, 1),
        "target_s": 180,
    }
    return report


def _print(report: dict) -> None:
    print(f"\n{'='*60}\n전체 loop 성능 벤치 (stages={report['stages']}, "
          f"repeat={report['repeat']})\n{'='*60}")
    for stage in ("retrieve", "judge"):
        if stage not in report:
            continue
        s = report[stage]
        w = s["wall_s"]
        print(f"\n[{stage}]  wall p50={w['p50']}s · p90={w['p90']}s · max={w['max']}s "
              f"(n={w['n']})")
        print(f"          LLM 콜 p50={s['llm_calls']['p50']} · max={s['llm_calls']['max']}")
        qk = "top1_accuracy" if stage == "retrieve" else "decision_accuracy"
        print(f"          품질 {qk}={s[qk]}")
    b = report["loop_budget_estimate"]
    print(f"\n[전체 loop 예산 시뮬]")
    print(f"  retrieve {b['retrieve_p90_s']}s + judge {b['judge_p90_per_candidate_s']}s "
          f"× {b['judge_candidates']}후보 = {b['retrieve_plus_judge_sequential_s']}s "
          f"(목표 {b['target_s']}s)")
    over = b["retrieve_plus_judge_sequential_s"] - b["target_s"]
    print(f"  → 목표 대비 {'초과 +' + str(round(over,1)) if over > 0 else '충족 ' + str(round(over,1))}s "
          f"({b['note']})")


def bench_parallel(topk: int) -> dict:
    """judge 순차 vs 병렬(judge_many) 벽시계 비교 — 병렬화 이득을 숫자로.

    같은 상위 K 케이스를 ① 하나씩 순차 ② ThreadPool 동시 실행하고 전체 벽시계와
    LLM 콜 수를 비교한다. mock은 LLM을 안 써 이득이 0(구조 검증용), 실LLM에서만
    배수가 보인다."""
    import contextvars
    from concurrent.futures import ThreadPoolExecutor

    golden = load_golden()
    cases = golden.get("judge_cases", [])[:topk]
    if not cases:
        return {"error": "judge_cases 없음"}

    run = progress.bind()
    t0 = time.time()
    for c in cases:
        _run_judge_once(c)
    seq_s = round(time.time() - t0, 1)
    seq_calls = run.llm_calls

    run = progress.bind()
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=min(len(cases), 4)) as ex:
        futs = []
        for c in cases:
            ctx = contextvars.copy_context()
            futs.append(ex.submit(ctx.run, _run_judge_once, c))
        for f in futs:
            f.result()
    par_s = round(time.time() - t0, 1)
    par_calls = run.llm_calls

    return {"candidates": len(cases), "sequential_s": seq_s, "parallel_s": par_s,
            "speedup": round(seq_s / par_s, 2) if par_s else 0,
            "llm_calls_seq": seq_calls, "llm_calls_par": par_calls}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stages", default="retrieve,judge",
                    help="쉼표 구분: retrieve,judge")
    ap.add_argument("--repeat", type=int, default=1, help="케이스당 반복(분산 측정)")
    ap.add_argument("--judge-topk", type=int, default=0,
                    help="judge 대상 상위 N (캐스케이드 시뮬, 0=전체)")
    ap.add_argument("--parallel-topk", type=int, default=0,
                    help=">0이면 상위 N을 순차 vs 병렬 벽시계 비교")
    ap.add_argument("--json", default="", help="리포트 JSON 저장 경로")
    a = ap.parse_args()

    if a.parallel_topk:
        report = bench_parallel(a.parallel_topk)
        print(f"\n{'='*60}\n순차 vs 병렬 판단 (상위 {a.parallel_topk})\n{'='*60}")
        print(f"  순차: {report['sequential_s']}s ({report['llm_calls_seq']}콜)")
        print(f"  병렬: {report['parallel_s']}s ({report['llm_calls_par']}콜)")
        print(f"  → {report['speedup']}배 빠름 (콜 수 동일 = 품질 무손실)")
        if a.json:
            with open(a.json, "w", encoding="utf-8") as f:
                json.dump(report, f, ensure_ascii=False, indent=2)
        return

    stages = [s.strip() for s in a.stages.split(",") if s.strip()]
    report = bench(stages, a.repeat, a.judge_topk)
    _print(report)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"\n리포트 저장: {a.json}")


if __name__ == "__main__":
    main()
