"""입력·타겟 시퀀스 프레이밍 (순수 파이썬 — id 리스트 위에서만 동작, torch 없음).

디렉터 스펙 그대로:
  입력   : <t1> A리서치 <t2> <t3> B리서치 <t4>
  타겟   : (프롬프트 전체 마스킹) + <t5> <점수토큰> <t6>

전체 시퀀스 = <t1> A <t2> <t3> B <t4> <t5> <점수> <t6>
labels     = [-100 …(프롬프트)…] + [<t5>, <점수>, <t6>]   (완결 구간만 학습)

teacher forcing에서 loss가 붙는 위치의 의미:
  <t4> 다음 → <t5> 예측 / <t5> 다음 → <점수> 예측 / <점수> 다음 → <t6> 예측.
즉 모델은 '두 리서치를 다 읽은 직후 점수를 내는' 능력을 배운다.

토큰화된 id 리스트만 받으므로 어떤 tokenizer와도 무관하게 테스트 가능하다.
"""
from dataclasses import dataclass

IGNORE_INDEX = -100


@dataclass(frozen=True)
class StructIds:
    """구조 토큰의 실제 vocab id (STRUCT_ROLES 순서와 동일)."""
    a_open: int
    a_close: int
    b_open: int
    b_close: int
    score_open: int
    score_close: int

    @classmethod
    def from_list(cls, ids):
        if len(ids) != 6:
            raise ValueError("구조 토큰 id는 6개여야 함")
        return cls(*ids)


def _truncate_pair(a_ids, b_ids, budget):
    """A·B 리서치 토큰을 예산 안으로 자른다. 완결 구간(구조·점수 토큰 9개 자리)은
    절대 건드리지 않는다 — 학습 신호가 잘리면 안 되므로 리서치 본문만 줄인다.
    A/B를 균등 비례로 자른다(한쪽만 긴 경우도 공평하게)."""
    if budget <= 0:
        return [], []
    total = len(a_ids) + len(b_ids)
    if total <= budget:
        return list(a_ids), list(b_ids)
    a_keep = max(1, round(budget * len(a_ids) / total))
    b_keep = max(1, budget - a_keep)
    return list(a_ids[:a_keep]), list(b_ids[:b_keep])


def build_example(a_ids, b_ids, score_token_id, struct: StructIds,
                  max_seq_len: int = 4096):
    """학습 예제 하나 → {input_ids, labels, length}. 완결 구간 3토큰은 항상 보존."""
    tail = [struct.score_open, score_token_id, struct.score_close]
    # 프레임 고정 토큰(구조 4개 + 완결 3개 = 7) 자리를 뺀 나머지가 리서치 예산
    budget = max_seq_len - 7
    a, b = _truncate_pair(a_ids, b_ids, budget)
    prompt = ([struct.a_open] + a + [struct.a_close]
              + [struct.b_open] + b + [struct.b_close])
    input_ids = prompt + tail
    labels = [IGNORE_INDEX] * len(prompt) + tail       # 완결 구간만 학습
    return {"input_ids": input_ids, "labels": labels, "length": len(input_ids)}


def build_prompt(a_ids, b_ids, struct: StructIds, max_seq_len: int = 4096):
    """추론용 프롬프트 — <t5>까지만. 모델이 다음 위치에서 점수 토큰을 예측한다."""
    budget = max_seq_len - 5
    a, b = _truncate_pair(a_ids, b_ids, budget)
    return ([struct.a_open] + a + [struct.a_close]
            + [struct.b_open] + b + [struct.b_close]
            + [struct.score_open])
