"""E11 학습 — 1.2B chat SFT (LoRA), assistant 구간만 loss (서버 전용).

스코어러와의 차이(정직):
  - 특수토큰 트릭 없음 — 표준 생성 SFT. 기댓값 readout도 없음.
  - LoRA 기본은 FFN-only지만, E4의 "attention 불필요"는 **분류에서의 발견**이다.
    생성 태스크에선 미검증 → --include-attention ablation을 반드시 같이 돌려라.
  - 마스킹: 프롬프트(system+user+generation_prompt)까지 -100, assistant 토큰만
    학습 — framing.py의 '완결 구간만 loss' 철학과 동일.

transformers 5.x: apply_chat_template(return_dict=True) 필수 (부록 A 참조).
"""
import argparse
import json
from pathlib import Path

import torch
from datasets import Dataset
from transformers import (AutoModelForCausalLM, AutoTokenizer, Trainer,
                          TrainingArguments)

IGNORE = -100


def encode_row(tok, messages, max_len):
    """전체 대화 토큰화 + 프롬프트 길이만큼 라벨 마스킹."""
    prompt_ids = tok.apply_chat_template(
        messages[:-1], tokenize=True, add_generation_prompt=True,
        return_dict=True, enable_thinking=False)["input_ids"]
    full_ids = tok.apply_chat_template(
        messages, tokenize=True, add_generation_prompt=False,
        return_dict=True, enable_thinking=False)["input_ids"]
    if len(full_ids) > max_len:                    # 타겟은 절대 안 자른다 —
        return None                                # 초과 샘플은 정직하게 드롭
    labels = [IGNORE] * len(prompt_ids) + full_ids[len(prompt_ids):]
    return {"input_ids": full_ids, "labels": labels, "length": len(full_ids)}


class Collator:
    def __init__(self, pad_id):
        self.pad_id = pad_id

    def __call__(self, batch):
        m = max(len(b["input_ids"]) for b in batch)
        ids, labs, attn = [], [], []
        for b in batch:
            pad = m - len(b["input_ids"])
            ids.append(b["input_ids"] + [self.pad_id] * pad)
            labs.append(b["labels"] + [IGNORE] * pad)
            attn.append([1] * len(b["input_ids"]) + [0] * pad)
        return {"input_ids": torch.tensor(ids), "labels": torch.tensor(labs),
                "attention_mask": torch.tensor(attn)}


def main():
    ap = argparse.ArgumentParser(description="E11 represent SFT (1.2B)")
    ap.add_argument("--train", required=True, help="represent_train.jsonl")
    ap.add_argument("--model-id", required=True)
    ap.add_argument("--output-dir", required=True)
    ap.add_argument("--epochs", type=float, default=3)
    ap.add_argument("--max-seq-len", type=int, default=4096)
    ap.add_argument("--lr", type=float, default=1e-4)
    ap.add_argument("--lora-r", type=int, default=16)
    ap.add_argument("--include-attention", action="store_true",
                    help="ablation — 생성 태스크에선 FFN-only가 미검증")
    ap.add_argument("--grad-accum", type=int, default=16)
    ap.add_argument("--seed", type=int, default=42)
    a = ap.parse_args()

    tok = AutoTokenizer.from_pretrained(a.model_id, trust_remote_code=True)
    model = AutoModelForCausalLM.from_pretrained(
        a.model_id, trust_remote_code=True, dtype=torch.bfloat16,
        device_map="auto")

    rows = []
    dropped = 0
    for line in Path(a.train).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        enc = encode_row(tok, r["messages"], a.max_seq_len)
        if enc is None:
            dropped += 1
            continue
        rows.append(enc)
    print(f"[데이터] 학습 {len(rows)} · 길이초과 드롭 {dropped}", flush=True)

    from peft import LoraConfig, get_peft_model
    targets = ["gate_proj", "up_proj", "down_proj"]
    if a.include_attention:
        targets += ["q_proj", "k_proj", "v_proj", "o_proj"]
    model = get_peft_model(model, LoraConfig(
        r=a.lora_r, lora_alpha=32, lora_dropout=0.05,
        target_modules=targets, bias="none", task_type="CAUSAL_LM"))
    tr = sum(p.numel() for p in model.parameters() if p.requires_grad)
    tot = sum(p.numel() for p in model.parameters())
    print(f"[모델] 학습 파라미터 {tr:,} / {tot:,} ({100*tr/tot:.3f}%) · "
          f"targets={targets}", flush=True)

    args = TrainingArguments(
        output_dir=a.output_dir, per_device_train_batch_size=1,
        gradient_accumulation_steps=a.grad_accum, learning_rate=a.lr,
        num_train_epochs=a.epochs, warmup_ratio=0.05, weight_decay=0.01,
        lr_scheduler_type="cosine", bf16=True, logging_steps=10,
        save_strategy="epoch", report_to=[], seed=a.seed,
        remove_unused_columns=False)
    pad = tok.pad_token_id if tok.pad_token_id is not None else tok.eos_token_id
    Trainer(model=model, args=args, train_dataset=Dataset.from_list(rows),
            data_collator=Collator(pad)).train()

    out = Path(a.output_dir)
    model.save_pretrained(out / "adapter")
    tok.save_pretrained(out / "adapter")
    print(f"[완료] {out}/adapter")


if __name__ == "__main__":
    main()
