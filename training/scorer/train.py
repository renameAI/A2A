"""학습 실행 (torch/transformers/peft/datasets, 서버 전용).

순서가 중요하다: 모델·토크나이저 로드 → 토큰 등록 → LoRA 적용 → 특수 행 unfreeze
→ 데이터셋 프레이밍 → Trainer. (LoRA 후 unfreeze — model_setup 주석 참조.)

패킹은 쓰지 않는다: 각 예제의 완결 구간(점수 토큰)이 시퀀스 끝에 정렬돼야 하는데,
패킹은 그 경계를 흐트러뜨린다. 대신 길이 정렬 배치로 패딩 낭비를 줄인다.
"""
import argparse
import json
from pathlib import Path

import torch
from datasets import Dataset
from transformers import Trainer, TrainingArguments

from .config import ScorerConfig
from .data import load_pairs, split_by_company, stratified_sample, validate
from .framing import IGNORE_INDEX, StructIds, build_example
from .model_setup import (apply_ffn_lora, load_model_and_tokenizer,
                          register_tokens, resolve_tokens, trainable_report,
                          unfreeze_special_rows)


def _encode(pairs, tokenizer, tokens, struct, cfg):
    score_str_to_id = {tokens.score_to_token(s):
                       tokenizer.convert_tokens_to_ids(tokens.score_to_token(s))
                       for s in range(0, 11)}
    rows = []
    for p in pairs:
        a_ids = tokenizer(p.a_text, add_special_tokens=False)["input_ids"]
        b_ids = tokenizer(p.b_text, add_special_tokens=False)["input_ids"]
        stok = score_str_to_id[tokens.score_to_token(p.score)]
        rows.append(build_example(a_ids, b_ids, stok, struct, cfg.max_seq_len))
    return Dataset.from_list(rows)


class _Collator:
    def __init__(self, pad_id):
        self.pad_id = pad_id

    def __call__(self, batch):
        maxlen = max(len(b["input_ids"]) for b in batch)
        input_ids, labels, attn = [], [], []
        for b in batch:
            pad = maxlen - len(b["input_ids"])
            input_ids.append(b["input_ids"] + [self.pad_id] * pad)
            labels.append(b["labels"] + [IGNORE_INDEX] * pad)
            attn.append([1] * len(b["input_ids"]) + [0] * pad)
        return {"input_ids": torch.tensor(input_ids),
                "labels": torch.tensor(labels),
                "attention_mask": torch.tensor(attn)}


def run(cfg: ScorerConfig, pairs_path: str):
    raw = load_pairs(pairs_path)
    report = validate(raw)
    pairs = report["valid"]
    train_pairs, held_pairs, dropped = split_by_company(
        pairs, cfg.held_frac, cfg.seed)
    train_pairs, samp = stratified_sample(train_pairs, cfg.per_bucket_cap, cfg.seed)
    print(f"[데이터] 유효 {len(pairs)} · 불량 {len(report['errors'])} · "
          f"train {len(train_pairs)}(샘플링 후) · held {len(held_pairs)} · "
          f"교차폐기 {len(dropped)}")
    print(f"[버킷] {samp['after']}")

    model, tokenizer = load_model_and_tokenizer(cfg)
    tokens = resolve_tokens(cfg)
    score_ids, struct_ids, special_ids = register_tokens(
        model, tokenizer, cfg, tokens)
    struct = StructIds.from_list(struct_ids)

    model = apply_ffn_lora(model, cfg)              # 1) LoRA 먼저
    unfreeze_special_rows(model, special_ids)       # 2) 그다음 특수 행 unfreeze
    print("[모델]", trainable_report(model))

    ds = _encode(train_pairs, tokenizer, tokens, struct, cfg)
    pad_id = tokenizer.pad_token_id
    if pad_id is None:
        pad_id = tokenizer.eos_token_id

    args = TrainingArguments(
        output_dir=cfg.output_dir,
        per_device_train_batch_size=cfg.per_device_batch_size,
        gradient_accumulation_steps=cfg.grad_accum_steps,
        learning_rate=cfg.learning_rate, num_train_epochs=cfg.num_epochs,
        warmup_ratio=cfg.warmup_ratio, weight_decay=cfg.weight_decay,
        lr_scheduler_type="cosine", bf16=cfg.bf16, logging_steps=10,
        save_strategy="epoch", report_to=[], seed=cfg.seed,
        deepspeed=cfg.deepspeed_config or None,
        remove_unused_columns=False, group_by_length=True,
        length_column_name="length")

    trainer = Trainer(model=model, args=args, train_dataset=ds,
                      data_collator=_Collator(pad_id))
    trainer.train()

    out = Path(cfg.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(out / "adapter")          # LoRA 어댑터
    # 학습된 특수 토큰 행만 따로 저장 (grad 훅으로 17행만 갱신됨)
    base = model.get_base_model()
    payload = {
        "score_ids": score_ids, "struct_ids": struct_ids,
        "score_tokens": list(tokens.score_tokens),
        "struct_tokens": list(tokens.struct_tokens),
        "in_rows": base.get_input_embeddings().weight[special_ids].detach().cpu()}
    if not base.config.tie_word_embeddings:
        payload["out_rows"] = \
            base.get_output_embeddings().weight[special_ids].detach().cpu()
    torch.save(payload, out / "special_token_weights.pt")
    (out / "held_pairs.json").write_text(
        json.dumps([p.__dict__ for p in held_pairs], ensure_ascii=False),
        encoding="utf-8")
    tokenizer.save_pretrained(out / "adapter")
    print(f"[완료] {out} — adapter · special_token_weights.pt · held_pairs.json")


def main():
    ap = argparse.ArgumentParser(description="EXAONE 관련도 스코어러 학습")
    ap.add_argument("--pairs", required=True, help="관련도 페어 JSONL")
    ap.add_argument("--model-id")
    ap.add_argument("--output-dir")
    ap.add_argument("--epochs", type=float)
    ap.add_argument("--include-attention", action="store_true",
                    help="LoRA를 어텐션에도 적용(ablation)")
    ap.add_argument("--tokens-json",
                    help="{'score':[11],'struct':[6]} 재사용 토큰 문자열")
    a = ap.parse_args()
    cfg = ScorerConfig()
    if a.model_id: cfg.model_id = a.model_id
    if a.output_dir: cfg.output_dir = a.output_dir
    if a.epochs: cfg.num_epochs = a.epochs
    if a.include_attention: cfg.lora_include_attention = True
    if a.tokens_json:
        t = json.loads(Path(a.tokens_json).read_text(encoding="utf-8"))
        cfg.score_token_strings, cfg.struct_token_strings = t["score"], t["struct"]
    run(cfg, a.pairs)


if __name__ == "__main__":
    main()
