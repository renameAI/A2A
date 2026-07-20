"""추론 — 두 기업 리서치/온톨로지 → 관련도 점수 (torch, 서버 전용).

기댓값 readout: 11개 점수 토큰 로짓만 softmax → 기댓값 Σ p_k·k = 연속·보정 점수.
argmax(이산)와 함께 반환한다. 순서성(6점 vs 7점) 정보를 살리는 게 핵심.
"""
from pathlib import Path

import torch
from peft import PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer

from .framing import StructIds, build_prompt


class RelatednessScorer:
    def __init__(self, base_model_id, run_dir, device="cuda"):
        run = Path(run_dir)
        self.tok = AutoTokenizer.from_pretrained(run / "adapter",
                                                 trust_remote_code=True)
        base = AutoModelForCausalLM.from_pretrained(
            base_model_id, trust_remote_code=True,
            dtype=torch.bfloat16, device_map=device)
        self.model = PeftModel.from_pretrained(base, run / "adapter")
        self.model.eval()

        payload = torch.load(run / "special_token_weights.pt", map_location=device)
        self.score_ids = payload["score_ids"]
        self.struct = StructIds.from_list(payload["struct_ids"])
        # 학습된 특수 토큰 행 복원 (grad 훅으로 갱신된 17행)
        inner = self.model.get_base_model()
        with torch.no_grad():
            inner.get_input_embeddings().weight[payload["score_ids"]
                + payload["struct_ids"]] = payload["in_rows"].to(device)
            if "out_rows" in payload and not inner.config.tie_word_embeddings:
                inner.get_output_embeddings().weight[payload["score_ids"]
                    + payload["struct_ids"]] = payload["out_rows"].to(device)
        self.device = device

    @torch.no_grad()
    def score(self, a_text: str, b_text: str, max_seq_len: int = 4096) -> dict:
        a_ids = self.tok(a_text, add_special_tokens=False)["input_ids"]
        b_ids = self.tok(b_text, add_special_tokens=False)["input_ids"]
        prompt = build_prompt(a_ids, b_ids, self.struct, max_seq_len)
        ids = torch.tensor([prompt], device=self.device)
        logits = self.model(ids).logits[0, -1]         # 마지막 위치 = 점수 예측
        score_logits = logits[self.score_ids]          # 11개만
        probs = torch.softmax(score_logits.float(), dim=-1)
        ks = torch.arange(len(self.score_ids), device=self.device).float()
        expected = float((probs * ks).sum())           # 연속 점수
        argmax = int(probs.argmax())                   # 이산 점수
        return {"score": expected, "argmax": argmax,
                "confidence": float(probs.max()),
                "distribution": [round(float(x), 3) for x in probs]}
