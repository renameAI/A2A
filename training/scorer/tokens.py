"""점수·구조 특수 토큰의 논리적 매핑 (순수 파이썬 — torch 없음).

디렉터 설계: 0~10점을 EXAONE voca의 '미학습 토큰' 11개에 지정하고, 입력 구조를
감싸는 미학습 토큰 6개를 둔다. 여기서는 '어떤 토큰 문자열이 몇 점인가'라는 논리만
담당한다 — 실제로 어떤 vocab id가 미학습(예약)인지 고르는 일은 tokenizer가 필요하므로
model_setup.py에서 한다.

토큰 문자열 규약: 서버의 실제 EXAONE tokenizer가 이미 가진 예약/미학습 토큰을
'재사용'하는 게 기본(임베딩 리사이즈 불필요). 재사용할 17개 토큰 문자열은 설정으로
주입한다. 아래 DEFAULT_*는 '새로 추가'하는 폴백 경로에서만 쓰는 이름이다.
"""
from dataclasses import dataclass

SCORE_MIN = 0
SCORE_MAX = 10
N_SCORE = SCORE_MAX - SCORE_MIN + 1          # 11
N_STRUCT = 6

# 구조 토큰의 역할(순서 고정) — 입력 프레이밍에 쓰인다.
#   A_OPEN  … A_CLOSE  B_OPEN  … B_CLOSE  SCORE_OPEN  <score>  SCORE_CLOSE
STRUCT_ROLES = ("A_OPEN", "A_CLOSE", "B_OPEN", "B_CLOSE",
                "SCORE_OPEN", "SCORE_CLOSE")

# 새 토큰을 '추가'하는 폴백 경로용 기본 문자열 (재사용 경로에선 안 씀).
DEFAULT_SCORE_TOKENS = tuple(f"[|match_score_{k}|]" for k in range(N_SCORE))
DEFAULT_STRUCT_TOKENS = tuple(f"[|match_{r.lower()}|]" for r in STRUCT_ROLES)


@dataclass(frozen=True)
class ScorerTokens:
    """점수·구조 토큰의 문자열 집합. score_tokens[k] = k점 토큰."""
    score_tokens: tuple           # len 11, index == 점수
    struct_tokens: tuple          # len 6, STRUCT_ROLES 순서

    def __post_init__(self):
        if len(self.score_tokens) != N_SCORE:
            raise ValueError(f"점수 토큰은 {N_SCORE}개여야 함 (받음 {len(self.score_tokens)})")
        if len(self.struct_tokens) != N_STRUCT:
            raise ValueError(f"구조 토큰은 {N_STRUCT}개여야 함 (받음 {len(self.struct_tokens)})")
        allt = list(self.score_tokens) + list(self.struct_tokens)
        if len(set(allt)) != len(allt):
            raise ValueError("점수·구조 토큰에 중복이 있음 (17개 모두 고유해야 함)")

    def score_to_token(self, score: int) -> str:
        if not (SCORE_MIN <= score <= SCORE_MAX):
            raise ValueError(f"점수는 {SCORE_MIN}~{SCORE_MAX} 범위 (받음 {score})")
        return self.score_tokens[score - SCORE_MIN]

    def token_to_score(self, token: str) -> int:
        return self.score_tokens.index(token) + SCORE_MIN

    def role(self, name: str) -> str:
        return self.struct_tokens[STRUCT_ROLES.index(name)]

    @property
    def all_tokens(self) -> list:
        return list(self.score_tokens) + list(self.struct_tokens)


def default_tokens() -> ScorerTokens:
    """새 토큰 추가 경로용 기본 집합."""
    return ScorerTokens(DEFAULT_SCORE_TOKENS, DEFAULT_STRUCT_TOKENS)
