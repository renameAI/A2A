"""로컬 EXAONE 백엔드 — 리서치 생성·페어 채점을 서버 GPU에서 (외부 API 0).

디렉터 요구: 4천 기업 데이터를 외부 API 없이 만든다. EXAONE엔 웹 검색이 없으므로
리서치 텍스트는 '기업명 + 섹터'로부터 EXAONE 내부 지식(컷오프 2024.11)으로 생성한다.
대기업은 정확하고 무명 소형주는 추정이 섞이므로, 생성물에 그 불확실성을 남긴다.

자기증류 주의: EXAONE-32B가 채점 → EXAONE-스코어러가 학습 = 에코 챔버 특성.
대신 실제 기업명·섹터가 앵커라 순수 합성보다 낫고, 완전 자립(무API)이다.

모델은 무겁게 한 번만 로드해 재사용한다(LocalExaone 싱글턴처럼 쓴다).
"""
import json
import re

import torch


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
        sys = ("너는 B2B 매칭 애널리스트다. 두 기업이 '사업 파트너로서 얼마나 "
               "관련(보완) 있는가'를 0~10으로 매긴다. 유사도가 아니라 보완성 기준 — "
               "한쪽의 산출물/역량이 다른 쪽의 결핍/수요를 메우면 높다. 동종 경쟁사는 낮다.\n"
               "0~2=무관/경쟁, 3~5=약한 접점, 6~7=뚜렷한 보완, 8~10=강한 보완.\n"
               '반드시 JSON 하나로만 답하라: {"score": <0~10 정수>, "reason": "<한 문장>"}')
        user = (f"[기업 A: {a_name}]\n{a_text[:1200]}\n\n"
                f"[기업 B: {b_name}]\n{b_text[:1200]}\n\nJSON으로 답하라.")
        raw = self._chat(sys, user, max_new=150, temperature=0.2)
        try:
            s = raw.find("{"); e = raw.rfind("}")
            d = json.loads(raw[s:e + 1])
            return {"score": max(0, min(10, int(d["score"]))),
                    "reason": str(d.get("reason", ""))[:200]}
        except Exception:                          # noqa: BLE001
            m = re.search(r"\b([0-9]|10)\b", raw)   # JSON 실패 시 숫자 폴백
            return {"score": int(m.group(1)), "reason": raw[:120]} if m else None
