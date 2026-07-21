"""로컬 EXAONE 백엔드 — 리서치 생성·페어 채점을 서버 GPU에서 (외부 API 0).

디렉터 요구: 4천 기업 데이터를 외부 API 없이 만든다. EXAONE엔 웹 검색이 없으므로
리서치 텍스트는 '기업명 + 섹터'로부터 EXAONE 내부 지식(컷오프 2024.11)으로 생성한다.
대기업은 정확하고 무명 소형주는 추정이 섞이므로, 생성물에 그 불확실성을 남긴다.

자기증류 주의: EXAONE-32B가 채점 → EXAONE-스코어러가 학습 = 에코 챔버 특성.
대신 실제 기업명·섹터가 앵커라 순수 합성보다 낫고, 완전 자립(무API)이다.

모델은 무겁게 한 번만 로드해 재사용한다(LocalExaone 싱글턴처럼 쓴다).
"""
import torch

from .pair_protocol import PAIR_SYSTEM, pair_user, parse_score


class LocalExaone:
    def __init__(self, model_path, device_map="auto", max_new_tokens=400):
        from transformers import AutoModelForCausalLM, AutoTokenizer
        self.tok = AutoTokenizer.from_pretrained(model_path, trust_remote_code=True)
        self.model = AutoModelForCausalLM.from_pretrained(
            model_path, trust_remote_code=True, dtype=torch.bfloat16,
            device_map=device_map)
        self.model.eval()
        self.max_new_tokens = max_new_tokens

    @torch.no_grad()
    def _chat(self, system, user, max_new=None, temperature=0.4):
        msgs = [{"role": "system", "content": system},
                {"role": "user", "content": user}]
        # transformers 5.x는 BatchEncoding(dict)를 돌려주므로 return_dict=True로 받아 **전달
        inputs = self.tok.apply_chat_template(
            msgs, tokenize=True, add_generation_prompt=True,
            return_tensors="pt", return_dict=True,
            enable_thinking=False).to(self.model.device)
        prompt_len = inputs["input_ids"].shape[1]
        out = self.model.generate(
            **inputs, max_new_tokens=max_new or self.max_new_tokens,
            do_sample=temperature > 0, temperature=temperature or None,
            top_p=0.9, pad_token_id=self.tok.eos_token_id)
        text = self.tok.decode(out[0][prompt_len:], skip_special_tokens=True)
        return text.strip()

    def research(self, name, sector) -> str:
        """기업명+섹터 → 리서치 텍스트. 아는 것과 추정을 구분하도록 지시."""
        sys = ("너는 기업 분석가다. 주어진 기업명과 섹터로 B2B 매칭 판단에 쓸 간결한 "
               "소개를 3~5문장으로 쓴다. 아는 사실과 추정을 구분하고, 확실치 않으면 "
               "'추정'이라고 밝힌다. 사업 영역·주력 제품/서비스·전형적 고객을 담아라.")
        user = f"기업명: {name}\n섹터: {sector}\n\n이 기업 소개를 작성하라."
        return self._chat(sys, user, max_new=350, temperature=0.4)

    def score_pair(self, a_name, a_text, b_name, b_text) -> "dict | None":
        """두 기업 → 0~10 보완 관련도 (JSON)."""
        raw = self._chat(PAIR_SYSTEM, pair_user(a_name, a_text, b_name, b_text),
                         max_new=150, temperature=0.2)
        return parse_score(raw)
