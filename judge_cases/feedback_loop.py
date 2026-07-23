# -*- coding: utf-8 -*-
"""
feedback_loop.py — new-judge 피드백 입력 창구(②) + 온톨로지 조율기(③)
==================================================================
역할:
  ② 입력 창구 — 인간 사용자가 (a) 결론 설명서·제안 메일에 대한 피드백,
     (b) 실제 제안 메일 발송 후 받은 '실제 회신'을 입력한다.
     → feedback_ledger.jsonl 에 provenance 와 함께 축적.
  ③ 조율기 — 입력된 피드백을 세션의 가설 카드(evidence_needed)와 대조해
     explore 가설을 채점(확증/반증)하고, buyer/seller 온톨로지의 판단 조건·
     질문 방식에 대한 '조정 규칙'을 제안한다. 인간 승인(기본) 후
     ontology_adjustments.json 에 반영 → negotiation_sim.py 가 다음 실행부터
     자동 로드해 프롬프트에 '우선 규칙'으로 주입한다.

설계 원칙 ("학습 없는 학습" 루프):
  explore 가설 → 현실 피드백으로 채점 → exploit 규칙 승격/기각 → 조정 계층 갱신.
  정규 YAML 온톨로지는 불변(감사 가능), 조정은 별도 계층에 origin 피드백 id 와
  함께 기록되어 언제든 롤백 가능하다.

사용:
  대화형:      python feedback_loop.py
  비대화형:    python feedback_loop.py --type market_reply --text "실제 회신 내용..."
  파일 입력:   python feedback_loop.py --type human_feedback --file fb.txt
  세션 지정:   --session negotiation_xxx_session.json (생략 시 최신 세션)
  자동 승인:   --auto-apply (검토 없이 반영 — 테스트용)
==================================================================
"""

import json
import glob
import argparse
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import negotiation_sim as ns   # chat_json·make_client·온톨로지 요약·경로 재사용

BASE_DIR = Path(__file__).resolve().parent
LEDGER_PATH = BASE_DIR / "feedback_ledger.jsonl"
ADJ_PATH = ns.ADJUSTMENTS_PATH

FB_TYPES = {
    "human_feedback": "사람 피드백 (결론 설명서·제안 메일·판단 과정에 대한 의견)",
    "market_reply": "실제 회신 (제안 메일 발송 후 상대 회사가 보낸 실제 답장/결과)",
}
ABOUT_CHOICES = ["explanation", "email_seller", "email_buyer", "decision", "reply", "other"]


# ==================================================================
# ② 입력 창구
# ==================================================================
def read_multiline(prompt: str) -> str:
    print(f"{prompt}\n  (여러 줄 가능 — 빈 줄에서 Enter 로 제출)")
    lines: List[str] = []
    while True:
        try:
            line = input("  > ")
        except EOFError:
            break
        if line == "":
            if lines:
                break
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def latest_session() -> Optional[Path]:
    files = sorted(glob.glob(str(BASE_DIR / "negotiation_*_session.json")),
                   key=lambda p: Path(p).stat().st_mtime, reverse=True)
    return Path(files[0]) if files else None


def next_fb_id() -> str:
    n = 0
    if LEDGER_PATH.exists():
        n = sum(1 for _ in LEDGER_PATH.open(encoding="utf-8"))
    return f"fb-{n + 1:04d}"


def record_feedback(session_path: Path, fb_type: str, about: str, content: str) -> Dict[str, Any]:
    entry = {
        "id": next_fb_id(),
        "ts": datetime.now().isoformat(timespec="seconds"),
        "session": session_path.name,
        "type": fb_type,                      # human_feedback | market_reply
        "about": about,                       # explanation|email_seller|email_buyer|decision|reply|other
        "content": content,
        "provenance": "observed" if fb_type == "market_reply" else "human",
        "processed": False,
    }
    with LEDGER_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    print(f"[기록] {entry['id']} → {LEDGER_PATH.name} (provenance={entry['provenance']})")
    return entry


# ==================================================================
# ③ 조율기 — 가설 채점 + 조정 규칙 제안 → 승인 → 반영
# ==================================================================
TUNER_SYS = f"""\
너는 new-judge 의 '온톨로지 조율자'다. 협상 세션의 판단 결과와, 그에 대한 새 피드백
(사람 의견 또는 실제 시장 회신)을 대조하여 온톨로지를 어떻게 조율할지 제안한다.

[현행 구매자 온톨로지]
{ns.BUYER_ONTOLOGY_SUMMARY}

[현행 판매자 온톨로지]
{ns.SELLER_ONTOLOGY_SUMMARY}

작업:
1) 가설 채점: 세션의 각 가설 카드(특히 explore)에 대해 이 피드백이
   confirmed(확증) / refuted(반증) / unaffected(무관) 인지 판정 — 가설의
   evidence_needed 와 피드백 내용을 대조해 근거를 적는다.
2) 조정 규칙 제안: 피드백이 드러낸 판단·질문·조건의 결함/개선점을,
   해당 basis 를 지목한 '실행 가능한 한 문장 규범'으로 만든다.
   예) "(BB5) 리빙 브랜드 유형에는 실물 샘플 3종이 아니라 전체 컬렉션 룩북을
        1차 증거로 요구하라" / "(SB5) 첫 CTA 에 로열티 요율 범위를 선제 포함하라"
   - 근거 없는 규칙 금지. 피드백이 지지하는 것만. 0개여도 된다.
   - market_reply(실제 회신)가 human_feedback 보다 강한 근거다.
   - ★각 규칙에 반드시 scope 를 붙여라: 이 규칙이 유효한 도메인·거래 유형의 범위.
     피드백은 특정 도메인의 사례이므로 기본은 global=false + 그 도메인 서술.
     도메인과 무관한 보편 원칙(예: '한계는 정직하게 공개')임이 명백할 때만 global=true.
3) 가설 갱신: 확증된 explore → promote_to_exploit / 반증 → retire /
   피드백이 새 의문을 열면 new_explore(반드시 evidence_needed 포함).

반드시 JSON 하나만 출력:
{{"hypothesis_scoring": [{{"statement": "<세션 가설 statement 앞부분>",
                           "verdict": "confirmed|refuted|unaffected", "reason": "<근거 1문장>"}}],
  "buyer_rules": [{{"basis": "<BB id>", "rule": "<실행 가능한 규범 1문장>", "reason": "<근거>",
                    "scope": {{"global": false, "domain": "<유효 도메인·거래 유형 1문장>"}}}}],
  "seller_rules": [{{"basis": "<SB id>", "rule": "<규범 1문장>", "reason": "<근거>",
                     "scope": {{"global": false, "domain": "<유효 범위>"}}}}],
  "hypothesis_updates": [{{"action": "promote_to_exploit|retire|new_explore",
                           "statement": "<가설>", "evidence_needed": "<new_explore 필수>",
                           "reason": "<근거>"}}]}}"""


def load_adj() -> Dict[str, Any]:
    if ADJ_PATH.exists():
        return json.loads(ADJ_PATH.read_text(encoding="utf-8"))
    return {"version": 1, "buyer_rules": [], "seller_rules": [],
            "hypothesis_updates": [], "history": []}


def propose_adjustments(client, session: Dict[str, Any], entry: Dict[str, Any]) -> Dict[str, Any]:
    ctx = (f"[세션] 판매자={session['seller_company']} / 구매자={session['buyer_company']}\n"
           f"[구매자 결정] {json.dumps(session['buyer_decision'], ensure_ascii=False)}\n"
           f"[판매자 outcome] {json.dumps(session['seller_outcome'], ensure_ascii=False)}\n"
           f"[세션 가설 카드]\n{json.dumps(session.get('hypotheses', []), ensure_ascii=False, indent=1)}\n\n"
           f"[새 피드백] type={entry['type']} (provenance={entry['provenance']}) "
           f"about={entry['about']}\n{entry['content']}")
    return ns.chat_json(client, ns.JUDGE_MODEL, TUNER_SYS, ctx, temperature=0.2, max_tokens=4000)


def sanitize_proposal(p: Dict[str, Any]) -> Dict[str, Any]:
    out = {"hypothesis_scoring": [], "buyer_rules": [], "seller_rules": [], "hypothesis_updates": []}
    for h in p.get("hypothesis_scoring", []) or []:
        if isinstance(h, dict) and h.get("statement"):
            v = h.get("verdict") if h.get("verdict") in ("confirmed", "refuted", "unaffected") else "unaffected"
            out["hypothesis_scoring"].append({"statement": str(h["statement"])[:160],
                                              "verdict": v, "reason": str(h.get("reason", ""))})
    for side in ("buyer_rules", "seller_rules"):
        for r in p.get(side, []) or []:
            if isinstance(r, dict) and r.get("rule"):
                sc = r.get("scope") if isinstance(r.get("scope"), dict) else {}
                scope = {"global": bool(sc.get("global", False)),
                         "domain": str(sc.get("domain") or "(도메인 미지정 — 심사 시 규칙 본문으로 판정)")}
                out[side].append({"basis": str(r.get("basis", "?")), "rule": str(r["rule"]),
                                  "reason": str(r.get("reason", "")), "scope": scope})
    for u in p.get("hypothesis_updates", []) or []:
        if isinstance(u, dict) and u.get("statement") and u.get("action") in (
                "promote_to_exploit", "retire", "new_explore"):
            card = {"action": u["action"], "statement": str(u["statement"]),
                    "reason": str(u.get("reason", ""))}
            if u["action"] == "new_explore":
                card["evidence_needed"] = str(u.get("evidence_needed") or "(미기재 — 지정 필요)")
            out["hypothesis_updates"].append(card)
    return out


def apply_adjustments(proposal: Dict[str, Any], entry: Dict[str, Any],
                      auto: bool) -> None:
    adj = load_adj()
    # 제안 표시
    print("\n=== 조율 제안 ===")
    print("[가설 채점]")
    for h in proposal["hypothesis_scoring"]:
        print(f"  · {h['verdict']:11s} | {h['statement'][:60]} — {h['reason'][:60]}")
    items: List[tuple] = []
    for side, key in (("buyer", "buyer_rules"), ("seller", "seller_rules")):
        for r in proposal[key]:
            items.append((key, r))
    for u in proposal["hypothesis_updates"]:
        items.append(("hypothesis_updates", u))
    if not items:
        print("[조정 규칙 제안 없음] — 피드백이 기존 규범을 바꿀 근거가 되지 않음")
        return
    print("[조정 제안]")
    for i, (key, it) in enumerate(items, 1):
        if key.endswith("_rules"):
            sc = it.get("scope", {})
            scope_str = "전역" if sc.get("global") else f"범위: {sc.get('domain', '?')[:50]}"
            print(f"  {i}. [{key[:-6]}·{it['basis']}] {it['rule']}\n"
                  f"     근거: {it['reason'][:70]} | {scope_str}")
        else:
            print(f"  {i}. [가설·{it['action']}] {it['statement'][:70]}")

    # 승인
    if auto:
        chosen = list(range(1, len(items) + 1))
        print("(--auto-apply: 전체 반영)")
    else:
        ans = input("\n반영할 번호(쉼표구분, a=전체, n=취소) > ").strip().lower()
        if ans in ("n", ""):
            print("반영 취소 — 원장에는 기록됨(미처리 상태)")
            return
        chosen = list(range(1, len(items) + 1)) if ans == "a" else \
            [int(x) for x in ans.replace(" ", "").split(",") if x.isdigit()]

    applied_ids = []
    for i in chosen:
        if not (1 <= i <= len(items)):
            continue
        key, it = items[i - 1]
        if key.endswith("_rules"):
            side = key[:-6]
            rid = f"ADJ-{side[0].upper()}{len(adj[key]) + 1:03d}"
            adj[key].append({"id": rid, "basis": it["basis"], "rule": it["rule"],
                             "reason": it["reason"],
                             "scope": it.get("scope", {"global": False,
                                                       "domain": "(도메인 미지정)"}),
                             "origin": entry["id"], "ts": entry["ts"], "status": "active"})
            applied_ids.append(rid)
        else:
            it2 = dict(it); it2.update({"origin": entry["id"], "ts": entry["ts"]})
            adj["hypothesis_updates"].append(it2)
            applied_ids.append(f"HYP-{it['action']}")
    adj["history"].append({"feedback_id": entry["id"], "ts": entry["ts"],
                           "applied": applied_ids,
                           "scoring": proposal["hypothesis_scoring"]})
    ADJ_PATH.write_text(json.dumps(adj, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[반영 완료] {len(applied_ids)}건 → {ADJ_PATH.name}")
    print("  → 다음 negotiation_sim 실행부터 '[조정 규칙 로드]'로 자동 주입됩니다.")


# ==================================================================
# main
# ==================================================================
def main() -> None:
    ap = argparse.ArgumentParser(description="new-judge 피드백 입력 창구 + 온톨로지 조율기")
    ap.add_argument("--session", default=None, help="대상 세션 json (생략 시 최신)")
    ap.add_argument("--type", dest="fb_type", choices=list(FB_TYPES), default=None)
    ap.add_argument("--about", choices=ABOUT_CHOICES, default=None)
    ap.add_argument("--text", default=None, help="피드백/회신 본문")
    ap.add_argument("--file", default=None, help="피드백/회신 본문 파일")
    ap.add_argument("--auto-apply", action="store_true", help="승인 없이 반영(테스트)")
    ap.add_argument("--no-tune", action="store_true", help="기록만 하고 조율 생략")
    args = ap.parse_args()

    sp = Path(args.session) if args.session else latest_session()
    if not sp or not sp.exists():
        raise SystemExit("[오류] 세션 파일을 찾을 수 없습니다. negotiation_sim 을 먼저 실행하세요.")
    session = json.loads(sp.read_text(encoding="utf-8"))
    print(f"[세션] {sp.name}  ({session['seller_company']} ↔ {session['buyer_company']} | "
          f"결정={session['buyer_decision']['decision']})")

    # --- 입력 수집 (② 창구) ---
    fb_type = args.fb_type
    if not fb_type:
        print("\n입력 유형을 선택하세요:")
        for i, (k, v) in enumerate(FB_TYPES.items(), 1):
            print(f"  {i}. {k} — {v}")
        sel = input("  번호 > ").strip()
        fb_type = list(FB_TYPES)[int(sel) - 1] if sel in ("1", "2") else "human_feedback"
    about = args.about or ("reply" if fb_type == "market_reply" else "decision")
    if args.text:
        content = args.text
    elif args.file:
        content = Path(args.file).read_text(encoding="utf-8")
    else:
        label = "실제 회신 내용" if fb_type == "market_reply" else "피드백 내용"
        content = read_multiline(f"\n{label}을 입력하세요")
    if not content:
        raise SystemExit("[오류] 내용이 비어 있습니다.")

    entry = record_feedback(sp, fb_type, about, content)

    # --- 조율 (③) ---
    if args.no_tune:
        print("(--no-tune: 조율 생략 — 원장에만 기록)")
        return
    client = ns.make_client()
    proposal = sanitize_proposal(propose_adjustments(client, session, entry))
    apply_adjustments(proposal, entry, auto=args.auto_apply)
    ns.report_cost()


if __name__ == "__main__":
    main()
