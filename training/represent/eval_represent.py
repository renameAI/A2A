"""E11 평가 — held 회사(처음 보는 곳)에서 학생(1.2B)의 represent 품질 (서버 전용).

지표 3층 (전부 자동 — 사람 개입 0):
  1) 스키마 유효율   : JSON 파싱 + 필수 필드 존재 (SLOT류의 1차 관문)
  2) 교사 합치       : 필드별 토큰 자카드 vs 교사 산출 (증류가 됐는가)
  3) 환각-stated율   : provenance=stated로 주장한 값의 토큰이 원문에 실제로
                       있는가 — R1 그라운딩의 경량판. represent 태스크의 핵심
                       정직성 지표 (stated인데 원문에 없으면 환각).

주의: 교사 합치는 상한이 교사 품질(자기증류 한계 — 스코어러와 동일 경고).
환각-stated율만이 교사와 독립적인 절대 지표다.
"""
import argparse
import json
import re
from pathlib import Path

import torch
from peft import PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer

REQUIRED = ("problem_solved", "solution", "target_customer", "portrait")
PORTRAIT = ("identity", "business_model", "edge", "stage_narrative",
            "assets", "gaps", "risk_signals")


def _tok(s):
    return set(re.findall(r"[가-힣A-Za-z0-9]{2,}", s or ""))


def _jaccard(a, b):
    A, B = _tok(a), _tok(b)
    return len(A & B) / max(1, len(A | B))


def _hallucinated_stated(pred: dict, source: str) -> tuple[int, int]:
    """stated 주장 중 원문 미근거 수 / stated 총수 — 값 토큰의 40% 미만이 원문에
    있으면 환각으로 계수 (represent._script_verifiable의 경량 근사)."""
    src = _tok(source)
    bad = tot = 0
    for f in REQUIRED[:3]:
        pf = pred.get(f) or {}
        if pf.get("provenance") == "stated":
            tot += 1
            val = _tok(pf.get("value", ""))
            if val and len(val & src) / len(val) < 0.4:
                bad += 1
    return bad, tot


def generate(model, tok, messages, max_new=900):
    inputs = tok.apply_chat_template(
        messages, tokenize=True, add_generation_prompt=True,
        return_tensors="pt", return_dict=True,
        enable_thinking=False).to(model.device)
    out = model.generate(**inputs, max_new_tokens=max_new, do_sample=False,
                         pad_token_id=tok.eos_token_id)
    return tok.decode(out[0][inputs["input_ids"].shape[1]:],
                      skip_special_tokens=True).strip()


def parse_json(text):
    try:
        s, e = text.find("{"), text.rfind("}")
        d = json.loads(text[s:e + 1])
        if not all(k in d for k in REQUIRED):
            return None
        return d
    except Exception:                              # noqa: BLE001
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-model", required=True)
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--held", required=True, help="represent_held.jsonl")
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--out", help="쌍별 결과 JSONL")
    a = ap.parse_args()

    rows = [json.loads(l) for l in
            Path(a.held).read_text(encoding="utf-8").splitlines() if l.strip()]
    rows = rows[:a.limit]

    tok = AutoTokenizer.from_pretrained(Path(a.run_dir) / "adapter",
                                        trust_remote_code=True)
    base = AutoModelForCausalLM.from_pretrained(
        a.base_model, trust_remote_code=True, dtype=torch.bfloat16,
        device_map="cuda")
    model = PeftModel.from_pretrained(base, Path(a.run_dir) / "adapter")
    model.eval()

    n_valid = 0
    agree_core, agree_port = [], []
    hall_bad = hall_tot = 0
    results = []
    for i, r in enumerate(rows):
        msgs = r["messages"][:2]                    # system + user만 (정답 제외)
        teacher = json.loads(r["messages"][2]["content"])
        source = r["messages"][1]["content"]
        raw = generate(model, tok, msgs)
        pred = parse_json(raw)
        rec = {"company": r["company"], "valid": pred is not None}
        if pred:
            n_valid += 1
            core = [_jaccard(pred[f].get("value", ""),
                             teacher[f]["value"]) for f in REQUIRED[:3]
                    if isinstance(pred.get(f), dict)]
            agree_core.append(sum(core) / max(1, len(core)))
            if pred.get("portrait") and teacher.get("portrait"):
                pj = [_jaccard(pred["portrait"].get(k, ""),
                               teacher["portrait"].get(k, "")) for k in PORTRAIT]
                agree_port.append(sum(pj) / len(pj))
            b, t = _hallucinated_stated(pred, source)
            hall_bad += b
            hall_tot += t
            rec.update({"core_agree": round(agree_core[-1], 3),
                        "hall_stated": f"{b}/{t}"})
        results.append(rec)
        if (i + 1) % 10 == 0:
            print(f"  … {i + 1}/{len(rows)}", flush=True)

    n = len(rows)
    print(f"\n[E11 평가] held {n}곳 (회사분리 — 학습에 없던 회사)")
    print(f"  1) 스키마 유효율    : {n_valid}/{n} ({100*n_valid/max(1,n):.0f}%)")
    if agree_core:
        print(f"  2) 교사 합치(핵심3) : 평균 {sum(agree_core)/len(agree_core):.3f}")
    if agree_port:
        print(f"     교사 합치(상7)   : 평균 {sum(agree_port)/len(agree_port):.3f}")
    print(f"  3) 환각-stated율    : {hall_bad}/{hall_tot} "
          f"({100*hall_bad/max(1,hall_tot):.0f}%) — stated 주장 중 원문 미근거")
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            for rec in results:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
