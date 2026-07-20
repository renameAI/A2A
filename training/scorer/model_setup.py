"""모델 구성 — 특수 토큰 등록 · 선택적 unfreeze · FFN LoRA (torch/peft, 서버 전용).

이 모듈은 로컬(서빙 레포)에 torch가 없어 import되지 않는다 — 서버에서만 쓴다.
설계의 핵심(순수 파이썬 테스트로는 못 잡는 부분)이라 주석을 촘촘히 단다.

세 가지가 정확히 맞아야 한다:
  1) EXAONE 4.0-32B는 tie_word_embeddings=False → 입력 임베딩과 lm_head가 별개.
     점수 토큰은 '예측'되므로 lm_head(출력) 행이, 구조 토큰은 '읽히므로' 입력 행이
     학습돼야 한다. 안전하게 17개 특수 토큰의 입력·출력 행을 모두 학습한다.
     (1.2B은 tied라 한 행렬 — config로 자동 분기.)
  2) requires_grad는 텐서 단위라 '일부 행만' 못 푼다 → 전체 임베딩을 unfreeze하되
     backward 훅으로 특수 토큰 외 행의 그래디언트를 0으로 만든다(행 단위 마스킹).
  3) LoRA를 먼저 씌우면 peft가 베이스를 다시 얼린다 → LoRA 적용 '후에' 임베딩
     unfreeze를 해야 둘 다 학습된다. 순서가 중요하다.
"""
import torch
from peft import LoraConfig, get_peft_model
from transformers import AutoModelForCausalLM, AutoTokenizer

from .tokens import ScorerTokens, default_tokens


def load_model_and_tokenizer(cfg):
    tok = AutoTokenizer.from_pretrained(cfg.model_id, trust_remote_code=True)
    model = AutoModelForCausalLM.from_pretrained(
        cfg.model_id, trust_remote_code=True,
        dtype=torch.bfloat16 if cfg.bf16 else torch.float32,
        device_map="auto")
    return model, tok


def resolve_tokens(cfg) -> ScorerTokens:
    if cfg.score_token_strings and cfg.struct_token_strings:
        return ScorerTokens(tuple(cfg.score_token_strings),
                            tuple(cfg.struct_token_strings))
    if cfg.reuse_tokens:
        raise ValueError(
            "reuse_tokens=True인데 재사용할 토큰 문자열이 비었다. "
            "서버 tokenizer에서 미학습/예약 토큰 17개를 골라 "
            "score_token_strings(11)·struct_token_strings(6)에 넣어라. "
            "후보는 find_unused_token_candidates()로 찾을 수 있다.")
    return default_tokens()   # 새로 추가하는 폴백


def find_unused_token_candidates(tokenizer, limit: int = 64) -> list:
    """예약/미학습으로 보이는 토큰 문자열 후보를 뽑는다(사람이 확인 후 채택).

    휴리스틱: 흔한 예약 패턴('reserved', 'unused', 'special_token', 'extra_id'
    등)을 포함하는 토큰. 정답 보장은 못 하므로 반드시 사람이 확인한다."""
    import re
    pat = re.compile(r"reserved|unused|special_token|extra_id|placeholder|dummy",
                     re.IGNORECASE)
    vocab = tokenizer.get_vocab()   # {token_str: id}
    cands = sorted((t for t in vocab if pat.search(t)), key=lambda t: vocab[t])
    return cands[:limit]


def register_tokens(model, tokenizer, cfg, tokens: ScorerTokens):
    """토큰을 vocab에 확정하고 (필요시 추가·리사이즈) 특수 토큰 id를 돌려준다.
    반환: (score_ids[11], struct_ids[6], special_ids[17])."""
    strs = tokens.all_tokens
    if cfg.reuse_tokens:
        ids = tokenizer.convert_tokens_to_ids(strs)
        unk = tokenizer.unk_token_id
        missing = [s for s, i in zip(strs, ids) if i is None or i == unk]
        if missing:
            raise ValueError(f"재사용하려는 토큰이 vocab에 없음: {missing[:5]} … "
                             "find_unused_token_candidates()로 실재 토큰을 고르라.")
    else:
        tokenizer.add_special_tokens({"additional_special_tokens": strs})
        model.resize_token_embeddings(len(tokenizer))
        ids = tokenizer.convert_tokens_to_ids(strs)

    score_ids = ids[:len(tokens.score_tokens)]
    struct_ids = ids[len(tokens.score_tokens):]
    special_ids = list(ids)

    if cfg.mean_init_special:
        _mean_init_rows(model.get_input_embeddings().weight, special_ids)
        out = model.get_output_embeddings()
        if out is not None and not model.config.tie_word_embeddings:
            _mean_init_rows(out.weight, special_ids)
    return score_ids, struct_ids, special_ids


@torch.no_grad()
def _mean_init_rows(weight, row_ids):
    """지정 행을 '나머지 행 평균'으로 초기화 — 미학습 노이즈 대신 합리적 출발점."""
    mask = torch.ones(weight.size(0), dtype=torch.bool, device=weight.device)
    mask[row_ids] = False
    mean_vec = weight[mask].mean(dim=0)
    for r in row_ids:
        weight[r] = mean_vec


def apply_ffn_lora(model, cfg):
    targets = list(cfg.lora_target_modules)
    if cfg.lora_include_attention:              # ablation: 어텐션도 적응
        targets += ["q_proj", "k_proj", "v_proj", "o_proj"]
    lora = LoraConfig(
        r=cfg.lora_r, lora_alpha=cfg.lora_alpha, lora_dropout=cfg.lora_dropout,
        target_modules=targets, bias="none", task_type="CAUSAL_LM")
    return get_peft_model(model, lora)


def unfreeze_special_rows(model, special_ids):
    """LoRA 적용 '후' 호출 — 특수 토큰 임베딩 행만 학습되게 만든다.

    전체 임베딩(및 untied면 lm_head)을 requires_grad=True로 두고, backward 훅으로
    특수 토큰 외 모든 행의 grad를 0으로 눌러 사실상 17행만 갱신되게 한다.
    반환: 저장 시 필요한 (in_emb, out_emb 또는 None)."""
    base = model.get_base_model() if hasattr(model, "get_base_model") else model
    in_emb = base.get_input_embeddings()
    out_emb = base.get_output_embeddings()
    tied = base.config.tie_word_embeddings

    ids_t = None

    def make_hook(vocab_size, device):
        keep = torch.zeros(vocab_size, dtype=torch.bool)
        keep[special_ids] = True
        keep = keep.to(device)

        def hook(grad):
            return grad * keep.unsqueeze(1)   # 유지 행만 grad 통과
        return hook

    in_emb.weight.requires_grad_(True)
    in_emb.weight.register_hook(make_hook(in_emb.weight.size(0), in_emb.weight.device))
    trained = {"in_emb": in_emb}
    if out_emb is not None and not tied:
        out_emb.weight.requires_grad_(True)
        out_emb.weight.register_hook(
            make_hook(out_emb.weight.size(0), out_emb.weight.device))
        trained["out_emb"] = out_emb
    return trained


def trainable_report(model) -> str:
    tot = sum(p.numel() for p in model.parameters())
    tr = sum(p.numel() for p in model.parameters() if p.requires_grad)
    return f"학습 파라미터 {tr:,} / 전체 {tot:,} ({100*tr/tot:.3f}%)"
