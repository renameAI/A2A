"""E11 교사 라벨링 — 엔진의 실제 represent 경로로 1,000사 리서치를 구조화한다.

학생(1.2B)이 배울 출력이 곧 프로덕션 형식이어야 하므로, 별도 프롬프트를 만들지
않고 **엔진의 extract_profile(다층 독해) 그대로**를 교사로 쓴다(형식 드리프트 0).

  입력: dataset/research_e9.db  (Gemini 검색 리서치 1,000사 — E9와 동일 코퍼스)
  교사: app.ingest.extractor.extract_profile  (LLM_PROVIDER=friendli → K-EXAONE)
  산출: dataset/represent_sft_raw.jsonl
        {company, research_text, target(핵심3필드+portrait7필드), r1_demoted, ...}

정직 게이트 (E10의 교훈 — 교사가 흔들리면 학생이 무너진다):
  --consistency N : 같은 회사 N곳을 2회 추출해 필드 일치율을 먼저 측정.
                    낮으면(경험칙 <0.6) 전체 라벨링을 멈추고 재설계.
  r1_demoted      : 교사 산출을 ground_profile(R1)로 검사한 강등 수를 함께 기록 —
                    교사 환각의 흔적을 데이터에 남긴다(드롭하지 않고 기록: 정직).

⚠️ K-EXAONE API 대량 호출 — 기본 --dry-run. Mac에서 실행(app/ 필요), 서버 불필요.
"""
import argparse
import json
import sqlite3
import time
from pathlib import Path

TARGET_FIELDS = ("problem_solved", "solution", "target_customer")
PORTRAIT_FIELDS = ("identity", "business_model", "edge", "stage_narrative",
                   "assets", "gaps", "risk_signals")


def _load_research(db_path, limit=0):
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT name, research_text FROM companies "
        "WHERE research_text IS NOT NULL AND length(research_text) > 200 "
        "ORDER BY name").fetchall()
    return rows[:limit] if limit else rows


def _teacher_extract(name, text):
    """엔진 실경로: 청킹 → extract_profile → R1 그라운딩. → (target dict, meta)."""
    from app.engine.llm import get_extractor
    from app.engine.represent import ground_profile
    from app.config import get_settings
    from app.ingest.chunking import chunk_text
    from app.ingest.extractor import extract_profile

    settings = get_settings()
    extractor = get_extractor(settings)
    if extractor is None:
        raise SystemExit("LLM 키 없음 — .env의 FRIENDLI_* 확인 (교사는 실 LLM 필수)")
    chunks = chunk_text(text, source=f"research:{name}")
    profile, open_questions, _evidence = extract_profile(chunks, extractor)
    tally = ground_profile(profile, text)          # R1 — 교사 환각 감사

    target = {}
    for f in TARGET_FIELDS:
        pf = getattr(profile, f)
        target[f] = {"value": pf.value, "provenance": pf.provenance.value}
    if profile.portrait is not None:
        target["portrait"] = {k: getattr(profile.portrait, k)
                              for k in PORTRAIT_FIELDS}
    else:
        target["portrait"] = None
    return target, {"r1_demoted": tally.get("demoted", 0),
                    "open_questions": len(open_questions)}


def _field_agreement(t1, t2, fields="full") -> float:
    """2회 추출 간 필드 토큰 중첩(0~1) 평균 — 교사 자기일관성의 근사.

    fields="core"면 핵심3필드만(SFT 스코프와 동일 기준으로 게이트 재확인 —
    portrait를 뺀 뒤 실제로 0.6을 넘는지는 별도 실측이 필요, 유추 금지)."""
    def tok(s):
        return set((s or "").split())
    scores = []
    for f in TARGET_FIELDS:
        a, b = tok(t1[f]["value"]), tok(t2[f]["value"])
        scores.append(len(a & b) / max(1, len(a | b)))
    if fields == "full":
        for f in PORTRAIT_FIELDS:
            if t1.get("portrait") and t2.get("portrait"):
                a, b = tok(t1["portrait"][f]), tok(t2["portrait"][f])
                scores.append(len(a & b) / max(1, len(a | b)))
    return sum(scores) / max(1, len(scores))


def run_consistency(rows, n, workers=4, fields="full") -> None:
    """학습 전 게이트 — 교사 자기일관성 + 동시성 실측 (병렬 스케일 확인 겸용).

    회사 단위로 병렬(회사당 2회는 순차). 벽시계로 엔드포인트가 동시 요청을
    실제로 소화하는지 측정 — 직렬화되면 전량 라벨링 계획 자체를 재설계해야 한다.
    fields="core"면 SFT 스코프(핵심3필드만)와 동일 기준으로 게이트를 검증한다."""
    import statistics
    import time as _t
    from concurrent.futures import ThreadPoolExecutor

    def one(item):
        name, text = item
        try:
            t1, _ = _teacher_extract(name, text)
            t2, _ = _teacher_extract(name, text)
            a = _field_agreement(t1, t2, fields)
            print(f"  {name}: 일치율 {a:.2f}", flush=True)
            return a
        except Exception as e:                     # noqa: BLE001 — 개별 실패는
            print(f"  ✗ {name}: {type(e).__name__}: {e}", flush=True)  # 격리
            return None

    t0 = _t.time()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        agr = [a for a in ex.map(one, rows[:n]) if a is not None]
    wall = _t.time() - t0
    if not agr:
        print("[일관성 게이트] 전원 실패 — 위 오류 확인 필요"); return
    mean = statistics.mean(agr)
    seq_est = n * 2 * 253                          # 스모크 실측 기반 직렬 추정
    print(f"[일관성 게이트({fields})] n={len(agr)} · 평균 {mean:.3f} · 최소 {min(agr):.2f}")
    print(f"[동시성 실측] 벽시계 {wall:.0f}s (workers={workers}) · "
          f"직렬추정 {seq_est}s · 스케일 {seq_est / max(1, wall):.1f}x")
    print("  → 품질: 평균 0.6 미만이면 전량 라벨링 중단 (E10 교훈)")
    print("  → 속도: 스케일 ~1x(직렬화)면 997사 계획 재설계 필요")


def run_label(rows, out_path, workers, min_agreement=0.0, fields="full") -> None:
    from concurrent.futures import ThreadPoolExecutor
    import threading
    lock = threading.Lock()
    done = set()
    out = Path(out_path)
    if out.exists():                               # 멱등 재개
        for line in out.read_text(encoding="utf-8").splitlines():
            if line.strip():
                done.add(json.loads(line)["company"])
    todo = [(n, t) for n, t in rows if n not in done]
    print(f"[라벨링] 총 {len(rows)} · 완료 {len(done)} · 예정 {len(todo)}", flush=True)

    def one(item):
        name, text = item
        try:
            target, meta = _teacher_extract(name, text)
            if min_agreement > 0:
                # 일관성 필터 — 2회째 추출해 자기일치율 확인. 흔들리는 회사는
                # 안정된 정답지를 못 주므로 드롭한다(ABION류만 남긴다).
                target2, _ = _teacher_extract(name, text)
                agr = _field_agreement(target, target2, fields)
                meta["self_agreement"] = round(agr, 3)
                if agr < min_agreement:
                    print(f"  ⊘ {name}: 일치율 {agr:.2f} < {min_agreement} — 드롭",
                          flush=True)
                    return None
                print(f"  ✓ {name}: 일치율 {agr:.2f} — 채택", flush=True)
        except Exception as e:                     # noqa: BLE001
            print(f"  ✗ {name}: {type(e).__name__}", flush=True)
            return None
        return {"company": name, "research_text": text, "target": target, **meta,
                "teacher": "k-exaone(friendli)/extract_profile",
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}

    n_ok = n_seen = 0
    with open(out_path, "a", encoding="utf-8") as f:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for row in ex.map(one, todo):
                n_seen += 1
                if not row:
                    continue
                with lock:
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")
                    n_ok += 1
                    if n_ok % 25 == 0:
                        print(f"  … 채택 {n_ok} / 시도 {n_seen}", flush=True)
    rate = f" (채택률 {100*n_ok/max(1,n_seen):.0f}%)" if min_agreement > 0 else ""
    print(f"[완료] 채택 {n_ok} / 시도 {n_seen}{rate} → {out_path}")


def main():
    ap = argparse.ArgumentParser(description="E11 교사 라벨링 (기본 dry-run)")
    ap.add_argument("--db", default="dataset/research_e9.db")
    ap.add_argument("--out", default="dataset/represent_sft_raw.jsonl")
    ap.add_argument("--limit", type=int, default=0, help="회사 수 상한 (프로브용)")
    ap.add_argument("--consistency", type=int, default=0,
                    help="N곳을 2회 추출해 자기일관성만 측정 (학습 전 게이트)")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--fields", choices=["core", "full"], default="full",
                    help="게이트 일치율 산정 범위 — sft_data.py --fields와 맞출 것")
    ap.add_argument("--min-agreement", type=float, default=0.0,
                    help=">0이면 라벨링 시 2회 추출해 자기일치율 이 값 미만인 회사는 "
                         "드롭(안정적 회사만 채택). 비용 2배, 정답지 품질 확보")
    ap.add_argument("--run", action="store_true", help="실제 API 실행")
    a = ap.parse_args()
    rows = _load_research(a.db, a.limit)
    if not a.run:
        print(f"[dry-run] 대상 {len(rows)}사 · 교사=엔진 extract_profile(K-EXAONE) · "
              f"산출 {a.out} — 실행은 --run")
        return
    if a.consistency:
        run_consistency(rows, a.consistency, a.workers, a.fields)
        return
    run_label(rows, a.out, a.workers, a.min_agreement, a.fields)


if __name__ == "__main__":
    main()
