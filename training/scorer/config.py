"""학습 설정 (순수 파이썬 dataclass). torch 파트가 이걸 읽어 모델을 구성한다."""
from dataclasses import dataclass, field


@dataclass
class ScorerConfig:
    # 모델 — 서버에 올라간 실제 경로로 바꿔 쓴다 (로컬 디렉터리 or HF id)
    model_id: str = "LGAI-EXAONE/EXAONE-4.0-32B"

    # 특수 토큰 — 재사용할 17개 문자열을 명시 주입(권장). 비우면 새로 추가(폴백).
    # 재사용 = 서버 tokenizer가 이미 가진 예약/미학습 토큰 id를 그대로 씀(리사이즈 없음).
    reuse_tokens: bool = True
    score_token_strings: list = field(default_factory=list)   # len 11 or []
    struct_token_strings: list = field(default_factory=list)  # len 6 or []
    mean_init_special: bool = True   # 미학습 토큰 임베딩을 기존 평균으로 초기화

    # LoRA — 기본 FFN만(SwiGLU: gate/up/down). 어텐션은 ablation용 옵션.
    lora_target_modules: list = field(
        default_factory=lambda: ["gate_proj", "up_proj", "down_proj"])
    lora_include_attention: bool = False   # True면 q/k/v/o_proj 추가(ablation)
    lora_r: int = 16
    lora_alpha: int = 32
    lora_dropout: float = 0.05

    # 학습 — 데이터가 수천~수만 쌍 규모라는 가정. epoch 과다는 과적합.
    max_seq_len: int = 4096
    learning_rate: float = 1e-4
    num_epochs: float = 2.0
    per_device_batch_size: int = 1
    grad_accum_steps: int = 16
    warmup_ratio: float = 0.03
    weight_decay: float = 0.0
    bf16: bool = True
    seed: int = 42

    # 데이터
    per_bucket_cap: int = 3000       # 점수 버킷별 상한(계층 샘플링)
    held_frac: float = 0.15

    # 산출
    output_dir: str = "training/runs/scorer"
    deepspeed_config: str = ""       # 비우면 미사용. 32B 풀 배치엔 ZeRO-3 권장.
