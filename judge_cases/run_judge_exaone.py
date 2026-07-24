"""박사님 judge를 K-EXAONE(Friendli)으로 실행 — 소버린 트랙 정합성 확인.

박사님 코드는 수정하지 않는다. chat()이 모든 LLM 호출의 단일 관문이므로
그 함수만 monkeypatch로 교체하고, OUT_DIR을 스크래치로 돌려 기존 Gemini
결과 파일을 덮어쓰지 않는다.
"""
import os
import sys
import json
import time
from pathlib import Path

BASE = "/Users/boram/a2a-matching-engine/judge_cases"
SCRATCH = Path(os.environ["SCRATCH_OUT"])
SCRATCH.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, BASE)
os.chdir(BASE)                     # 온톨로지 yaml 등 상대경로 로드용
import negotiation_sim as ns       # noqa: E402
import httpx                       # noqa: E402

TOKEN = os.environ["FRIENDLI_TOKEN"]
ENDPOINT = os.environ["FRIENDLI_ENDPOINT_ID"]
CALLS = {"n": 0, "fail": 0}


def exaone_chat(client, model, system, user, temperature=0.4, max_tokens=3000,
                json_mode=False, use_search=False):
    """chat()과 동일 시그니처. use_search는 EXAONE에 없어 무시(이 테스트엔 미사용)."""
    CALLS["n"] += 1
    last = None
    for attempt in range(3):
        try:
            r = httpx.post(
                "https://api.friendli.ai/dedicated/v1/chat/completions",
                headers={"Authorization": "Bearer " + TOKEN},
                json={"model": ENDPOINT, "temperature": temperature,
                      "max_tokens": max_tokens,
                      "messages": [{"role": "system", "content": system},
                                   {"role": "user", "content": user}],
                      "chat_template_kwargs": {"enable_thinking": False}},
                timeout=300)
            r.raise_for_status()
            return (r.json()["choices"][0]["message"]["content"] or "").strip()
        except Exception as e:      # noqa: BLE001
            last = e
            time.sleep(2 * (attempt + 1))
    CALLS["fail"] += 1
    raise RuntimeError("K-EXAONE 호출 실패: %s" % last)


ns.chat = exaone_chat
ns.make_client = lambda: None
ns.JUDGE_MODEL = "k-exaone-236b(friendli)"
ns.SPEAK_MODEL = "k-exaone-236b(friendli)"
ns.OUT_DIR = SCRATCH               # 기존 Gemini 산출물 보존

t0 = time.time()
try:
    ns.run(os.environ.get("SCN", "baseline"))
    err = None
except Exception as e:             # noqa: BLE001
    err = "%s: %s" % (type(e).__name__, e)
el = time.time() - t0

print("\n" + "=" * 60)
print("[EXAONE 실행 요약] 소요 %.0fs · LLM 호출 %d회 · 호출실패 %d"
      % (el, CALLS["n"], CALLS["fail"]))
if err:
    print("[중단] %s" % err)

# 산출 세션 검사
sess = sorted(SCRATCH.glob("*session.json"))
if not sess:
    print("[결과] 세션 파일 미생성")
else:
    d = json.load(open(sess[-1], encoding="utf-8"))
    bb = d.get("buyer_state", {})
    sb = d.get("seller_state", {})
    dec = (d.get("buyer_decision") or {}).get("decision")
    outc = (d.get("seller_outcome") or {}).get("outcome")
    print("[결과] 파일: %s" % sess[-1].name)
    print("  BB축 %d/10 · SB축 %d/10 채워짐" % (len(bb), len(sb)))
    print("  buyer_decision = %s  (Gemini 기준값: conditional)" % dec)
    print("  seller_outcome = %s" % outc)
    print("  rounds = %s" % d.get("rounds"))
    missing_bb = [k for k in ns.BB_IDS if k not in bb]
    print("  누락 BB축: %s" % (missing_bb or "없음"))
print("EXAONE_JUDGE_DONE")
