"""스모크 — 실제 EXAONE으로 model_setup+framing 통합을 몇 스텝만 검증 (Trainer 없이).

Trainer 전체를 돌리기 전에 핵심 위험(모델 로드·토큰 등록·행단위 unfreeze·FFN LoRA·
forward/backward)이 실제 모델에서 도는지 빠르게 확인한다. 성공하면 train.py로 간다.
"""
import argparse
import json
import sys

import torch

from .config import ScorerConfig
from .data import load_pairs, stratified_sample, validate
from .framing import StructIds, build_example
from .model_setup import (apply_ffn_lora, load_model_and_tokenizer,
                          register_tokens, resolve_tokens, trainable_report,
                          unfreeze_special_rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-id", required=True)
    ap.add_argument("--pairs", required=True)
    ap.add_argument("--tokens-json", required=True)
    ap.add_argument("--steps", type=int, default=3)
    a = ap.parse_args()

    cfg = ScorerConfig(model_id=a.model_id)
    t = json.loads(open(a.tokens_json).read())
    cfg.score_token_strings, cfg.struct_token_strings = t["score"], t["struct"]

    print("[1] 모델·토크나이저 로딩...", flush=True)
    model, tokenizer = load_model_and_tokenizer(cfg)
    print("    tie_word_embeddings:", model.config.tie_word_embeddings, flush=True)

    print("[2] 특수 토큰 등록 (재사용) + 평균 초기화...", flush=True)
    tokens = resolve_tokens(cfg)
    score_ids, struct_ids, special_ids = register_tokens(model, tokenizer, cfg, tokens)
    print("    score_ids:", score_ids, flush=True)
    print("    struct_ids:", struct_ids, flush=True)
    struct = StructIds.from_list(struct_ids)

    print("[3] FFN LoRA 적용...", flush=True)
    model = apply_ffn_lora(model, cfg)
    print("[4] 특수 토큰 행 unfreeze (LoRA 후)...", flush=True)
    unfreeze_special_rows(model, special_ids)
    print("    " + trainable_report(model), flush=True)

    print("[5] 데이터 몇 건 프레이밍...", flush=True)
    pairs = stratified_sample(validate(load_pairs(a.pairs))["valid"], 200, 42)[0][:8]
    score_tok_id = {s: tokenizer.convert_tokens_to_ids(tokens.score_to_token(s))
                    for s in range(11)}
    batch = []
    for p in pairs:
        ai = tokenizer(p.a_text, add_special_tokens=False)["input_ids"]
        bi = tokenizer(p.b_text, add_special_tokens=False)["input_ids"]
        batch.append(build_example(ai, bi, score_tok_id[p.score], struct, 512))

    print("[6] forward/backward %d 스텝..." % a.steps, flush=True)
    device = next(model.parameters()).device
    opt = torch.optim.AdamW([q for q in model.parameters() if q.requires_grad], lr=1e-4)
    model.train()
    for step in range(a.steps):
        ex = batch[step % len(batch)]
        ids = torch.tensor([ex["input_ids"]], device=device)
        lbl = torch.tensor([ex["labels"]], device=device)
        out = model(input_ids=ids, labels=lbl)
        out.loss.backward()
        # 특수 토큰 외 임베딩 행 grad가 0인지 확인 (행 마스킹 검증)
        emb = model.get_input_embeddings().weight
        if emb.grad is not None:
            nonzero_rows = (emb.grad.abs().sum(dim=1) > 0).nonzero().flatten().tolist()
            leaked = [r for r in nonzero_rows if r not in special_ids]
            tag = "OK(특수행만)" if not leaked else f"⚠누수 {leaked[:5]}"
        else:
            tag = "grad None"
        opt.step(); opt.zero_grad()
        print(f"    step {step}: loss={out.loss.item():.4f} · 임베딩 grad {tag}", flush=True)

    print("SMOKE_OK", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        traceback.print_exc()
        print("SMOKE_FAIL", flush=True)
        sys.exit(1)
