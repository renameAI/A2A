"""페어 관련도 스코어링 (step 1 후반) — 리서치된 기업쌍에 0~10점.

디렉터 스펙 1단계 후반: "저장한 기업들의 소개 자료를 claude에게 시켜 기업 간
관련도를 10점 만점으로 매겨 매칭 순서쌍에 기록". 그 점수가 스코어러의 학습 타겟.

⚠️ Claude API 대량 호출 — 실행은 AXR팀 협의 후. 기본 --dry-run.
⚠️ 키는 환경변수 ANTHROPIC_API_KEY 로만. 코드에 넣지 않는다.

★ 계층 샘플링을 위한 하드 포지티브/네거티브 마이닝:
  4000사 → 800만 쌍을 다 못 매긴다. 도메인 힌트로 '보완 가능성 있는 쌍'을
  우선 샘플링해 고득점 표본을 확보하고(스코어러 불균형 붕괴 방지), 무작위 쌍도
  섞어 저득점 기준선을 만든다. 이것이 dry-run 히스토그램이 경고한 쏠림의 해법.

출력: RelatednessPair JSONL — training/scorer/data.py가 그대로 소비.
채점 신뢰도: Claude 다중 표본 없이 단일 표본이면 요동이 크다. sample_k>1이면
같은 쌍을 k번 매겨 중앙값을 쓰고 표본 일치도를 기록한다(에코 챔버 완화 신호).
"""
import argparse
import json
import os
import random
import sqlite3
import time
from pathlib import Path

SCORE_SYS = """\
너는 B2B 매칭 애널리스트다. 두 기업의 리서치 요약을 읽고, 이 둘이 '사업 파트너로서
얼마나 관련(보완) 있는가'를 0~10점으로 매긴다. 유사도가 아니라 보완성 기준이다:
한쪽의 산출물/역량이 다른 쪽의 결핍/수요를 메우면 높다. 동종 경쟁사는 낮다.

기준선:
  0~2 = 무관하거나 순수 경쟁 관계
  3~5 = 약한 접점 (같은 산업이나 직접 거래 이유 약함)
  6~7 = 뚜렷한 보완 가능성 (공급-수요 또는 채널 매개)
  8~10 = 강한 보완 (직접 공급-수요, 명확한 거래 시나리오)

출력은 JSON 하나: {"score": <0~10 정수>, "reason": "<한 문장 근거>"}"""


def _pair_user(a_name, a_text, b_name, b_text) -> str:
    return (f"[기업 A: {a_name}]\n{a_text[:1500]}\n\n"
            f"[기업 B: {b_name}]\n{b_text[:1500]}\n\n"
            "두 기업의 보완 관련도를 0~10으로 매기고 JSON으로 답하라.")


def _parse(text) -> "dict | None":
    try:
        s = text.find("{"); e = text.rfind("}")
        d = json.loads(text[s:e + 1])
        return {"score": max(0, min(10, int(d["score"]))),
                "reason": str(d.get("reason", ""))[:200]}
    except Exception:                             # noqa: BLE001
        import re
        m = re.search(r"\b([0-9]|10)\b", text)    # JSON 실패 시 숫자 폴백
        return {"score": int(m.group(1)), "reason": text[:120]} if m else None


def _claude_scorer():
    """ANTHROPIC_API_KEY → (a,b)->dict 채점 함수."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise SystemExit("ANTHROPIC_API_KEY 환경변수가 없습니다 (키는 코드에 넣지 않음).")
    import anthropic
    client = anthropic.Anthropic(api_key=key)
    model = os.environ.get("SCORE_MODEL", "claude-opus-4-8")

    def score(a, b):
        msg = client.messages.create(
            model=model, max_tokens=200, system=SCORE_SYS,
            messages=[{"role": "user",
                       "content": _pair_user(a[0], a[1], b[0], b[1])}])
        text = "".join(blk.text for blk in msg.content if blk.type == "text")
        return _parse(text)
    return score, model


def _friendli_scorer(timeout: float = 120.0):
    """FRIENDLI_TOKEN/ENDPOINT_ID → K-EXAONE dedicated 채점 함수."""
    import httpx
    token = os.environ.get("FRIENDLI_TOKEN")
    endpoint = os.environ.get("FRIENDLI_ENDPOINT_ID")
    if not (token and endpoint):
        raise SystemExit("FRIENDLI_TOKEN/FRIENDLI_ENDPOINT_ID 환경변수가 없습니다.")

    def score(a, b):
        r = httpx.post(
            "https://api.friendli.ai/dedicated/v1/chat/completions",
            headers={"Authorization": f"Bearer {token}"},
            json={"model": endpoint, "temperature": 0.2, "max_tokens": 200,
                  "messages": [
                      {"role": "system", "content": SCORE_SYS},
                      {"role": "user",
                       "content": _pair_user(a[0], a[1], b[0], b[1])}],
                  "chat_template_kwargs": {"enable_thinking": False}},
            timeout=timeout)
        r.raise_for_status()
        return _parse(r.json()["choices"][0]["message"]["content"])
    return score, f"k-exaone-236b({endpoint[:12]}…)"


def _mine_pairs(companies: list, n_pairs: int, seed: int) -> list:
    """하드 포지티브(같은/인접 힌트 키워드) + 무작위 네거티브 혼합 샘플링."""
    if len(companies) < 2:        # 리서치 전이면 빈 계획 (크래시 대신)
        return []
    rng = random.Random(seed)

    def toks(c):
        return set((c.get("hints", "") + " " + c["name"]).lower().split())
    pos, neg, seen = [], [], set()
    idx = list(range(len(companies)))
    tries = 0
    while (len(pos) + len(neg)) < n_pairs and tries < n_pairs * 20:
        tries += 1
        i, j = rng.sample(idx, 2)
        key = tuple(sorted([i, j]))
        if key in seen:
            continue
        seen.add(key)
        shared = toks(companies[i]) & toks(companies[j])
        bucket = pos if len(shared) >= 1 else neg
        # 포지티브를 과반으로 (불균형 방지) — 목표의 55%까지 pos 우선
        if bucket is pos and len(pos) < int(n_pairs * 0.55):
            pos.append(key)
        elif bucket is neg and len(neg) < n_pairs - int(n_pairs * 0.55):
            neg.append(key)
    out = pos + neg
    # 폴백 — 힌트 공통토큰("KRX 상장 섹터…")으로 대부분 pos 분류되며 pos 캡(55%)에서
    # 조기 포화하면 목표 미달이 된다. 남은 자리는 무작위 유니크 쌍으로 채운다(리서치가
    # 풍부해 무작위 쌍도 유의미한 점수가 나옴 — 프로브에서 확인).
    max_pairs = len(companies) * (len(companies) - 1) // 2
    tries = 0
    while len(out) < min(n_pairs, max_pairs) and tries < n_pairs * 40:
        tries += 1
        i, j = rng.sample(idx, 2)
        key = tuple(sorted([i, j]))
        if key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def run(db_path, out_path, n_pairs, sample_k, mode, seed, dry_run,
        backend="friendli", workers=1) -> dict:
    conn = sqlite3.connect(db_path)
    rows = conn.execute("SELECT name, hints, research_text FROM companies "
                        "WHERE research_text IS NOT NULL").fetchall()
    companies = [{"name": r[0], "hints": r[1] or "", "text": r[2]} for r in rows]
    pairs = _mine_pairs(companies, n_pairs, seed)
    tally = {"companies": len(companies), "pairs_planned": len(pairs),
             "sample_k": sample_k, "mode": mode, "backend": backend,
             "dry_run": dry_run}
    if dry_run:
        print(f"[dry-run] 회사 {len(companies)} · 채점 예정 쌍 {len(pairs)} "
              f"(k={sample_k}, backend={backend}) — 실행하려면 --run")
        return tally
    if len(companies) < 2:
        raise SystemExit("리서치 DB에 기업이 2곳 미만 — 먼저 research.py --run 필요.")

    score_fn, model = (_friendli_scorer() if backend == "friendli"
                       else _claude_scorer())
    print(f"[채점] backend={backend} · model={model} · 쌍 {len(pairs)} · "
          f"k={sample_k} · workers={workers}", flush=True)

    def _one(pair):
        i, j = pair
        ca, cb = companies[i], companies[j]
        a = (ca["name"], ca["text"]); b = (cb["name"], cb["text"])
        scores = []
        for _ in range(sample_k):
            try:
                r = score_fn(a, b)
            except Exception as e:                # noqa: BLE001 — 개별 실패는 건너뜀
                print(f"  ✗ {ca['name']}×{cb['name']}: {type(e).__name__}",
                      flush=True)
                r = None
            if r:
                scores.append(r["score"])
        if not scores:
            return None
        scores.sort()
        median = scores[len(scores) // 2]
        return {"a_id": ca["name"], "a_text": ca["text"],
                "b_id": cb["name"], "b_text": cb["text"],
                "score": median, "mode": mode,
                "source": f"{model}-k{sample_k}",
                "sample_agreement": round(scores.count(median) / len(scores), 2)}

    import threading
    lock = threading.Lock()
    written = 0
    with open(out_path, "w", encoding="utf-8") as f:
        def _emit(row):
            nonlocal written
            if not row:
                return
            with lock:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
                written += 1
                if written % 50 == 0:
                    print(f"  … {written}/{len(pairs)} 채점", flush=True)
        if workers <= 1:
            for p in pairs:
                _emit(_one(p))
        else:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=workers) as ex:
                for row in ex.map(_one, pairs):
                    _emit(row)
    tally["written"] = written
    print(f"[완료] {written} 페어 → {out_path}", flush=True)
    return tally


def main() -> None:
    ap = argparse.ArgumentParser(
        description="페어 관련도 스코어링 (Claude) — 기본 dry-run, 실행은 AXR 협의 후")
    ap.add_argument("--db", default="dataset/research.db")
    ap.add_argument("--out", default="dataset/scorer_pairs.jsonl")
    ap.add_argument("--pairs", type=int, default=4000)
    ap.add_argument("--sample-k", type=int, default=1, help=">1이면 중앙값+일치도")
    ap.add_argument("--mode", default="research", choices=["research", "ontology"])
    ap.add_argument("--backend", default="friendli", choices=["friendli", "claude"],
                    help="채점 모델 — friendli(K-EXAONE-236B) 또는 claude")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--workers", type=int, default=1,
                    help="동시 API 호출 수 (I/O 대기라 6~8 권장)")
    ap.add_argument("--run", action="store_true", help="실제 API 실행 (없으면 dry-run)")
    a = ap.parse_args()
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    run(a.db, a.out, a.pairs, a.sample_k, a.mode, a.seed,
        dry_run=not a.run, backend=a.backend, workers=a.workers)


if __name__ == "__main__":
    main()
