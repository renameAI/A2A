"""EXAONE 스코어러 순수 파이썬 코어 테스트 (torch 없이 로컬 실행).

torch 파트(model_setup/train/infer)는 서버 전용이라 여기서 테스트하지 않는다 —
로컬에 torch가 없고, GPU 없이는 의미 있는 검증이 안 되기 때문. 대신 학습의
'정직성·정합성'을 좌우하는 순수 로직(토큰 매핑·시퀀스 마스킹·계층 샘플링·회사
분할)을 여기서 못박는다.
"""
import pytest

from training.scorer.data import (RelatednessPair, histogram, split_by_company,
                                  stratified_sample, validate)
from training.scorer.framing import (IGNORE_INDEX, StructIds, build_example,
                                     build_prompt)
from training.scorer.tokens import (N_SCORE, N_STRUCT, ScorerTokens,
                                    default_tokens)

STRUCT = StructIds(a_open=1001, a_close=1002, b_open=1003, b_close=1004,
                   score_open=1005, score_close=1006)


class TestTokens:
    def test_score_roundtrip(self):
        t = default_tokens()
        for s in range(0, 11):
            assert t.token_to_score(t.score_to_token(s)) == s

    def test_counts(self):
        t = default_tokens()
        assert len(t.score_tokens) == N_SCORE == 11
        assert len(t.struct_tokens) == N_STRUCT == 6

    def test_out_of_range_rejected(self):
        with pytest.raises(ValueError):
            default_tokens().score_to_token(11)

    def test_duplicate_rejected(self):
        with pytest.raises(ValueError):
            ScorerTokens(tuple(["<dup>"] * 11), tuple(f"<s{i}>" for i in range(6)))

    def test_roles(self):
        t = default_tokens()
        assert t.role("A_OPEN") == t.struct_tokens[0]
        assert t.role("SCORE_CLOSE") == t.struct_tokens[5]


class TestFraming:
    def test_masking_only_completion(self):
        ex = build_example([11, 12, 13], [21, 22], score_token_id=555,
                           struct=STRUCT, max_seq_len=100)
        # 완결 구간 = <score_open> <score> <score_close> 3토큰만 학습
        assert ex["labels"][-3:] == [1005, 555, 1006]
        assert all(x == IGNORE_INDEX for x in ex["labels"][:-3])

    def test_sequence_frame(self):
        ex = build_example([11], [21], 555, STRUCT, 100)
        assert ex["input_ids"] == [1001, 11, 1002, 1003, 21, 1004, 1005, 555, 1006]

    def test_truncation_preserves_tail(self):
        a = list(range(100, 400))     # 300 tokens
        b = list(range(400, 700))     # 300 tokens
        ex = build_example(a, b, 555, STRUCT, max_seq_len=50)
        assert ex["length"] <= 50
        assert ex["labels"][-3:] == [1005, 555, 1006]   # 완결 구간 보존
        assert ex["input_ids"][-3:] == [1005, 555, 1006]

    def test_prompt_stops_before_score(self):
        p = build_prompt([11], [21], STRUCT, 100)
        assert p == [1001, 11, 1002, 1003, 21, 1004, 1005]   # <score_open>에서 끝


def _pair(a, b, s, mode="research"):
    return RelatednessPair(a_id=a, a_text=f"{a} 리서치", b_id=b,
                           b_text=f"{b} 리서치", score=s, mode=mode)


class TestData:
    def test_validate_rejects_bad(self):
        pairs = [_pair("co1", "co2", 5), _pair("co3", "co4", 11),
                 _pair("co5", "co5", 3), RelatednessPair("x", "", "y", "t", 2)]
        rep = validate(pairs)
        assert len(rep["valid"]) == 1
        assert len(rep["errors"]) == 3

    def test_validate_dedup(self):
        # (A,B)와 (B,A)는 같은 쌍 — 하나만 유효
        rep = validate([_pair("a", "b", 5), _pair("b", "a", 7)])
        assert len(rep["valid"]) == 1

    def test_stratified_balances_and_caps(self):
        pairs = ([_pair(f"z{i}", f"w{i}", 0) for i in range(100)]   # 0점 100개
                 + [_pair(f"p{i}", f"q{i}", 8) for i in range(5)])   # 8점 5개
        sampled, rep = stratified_sample(pairs, per_bucket_cap=10, seed=1)
        assert rep["after"][0] == 10      # 0점은 10개로 제한
        assert rep["after"][8] == 5       # 8점은 전부 유지
        assert len(sampled) == 15

    def test_stratified_deterministic(self):
        pairs = [_pair(f"z{i}", f"w{i}", i % 11) for i in range(200)]
        a, _ = stratified_sample(pairs, 5, seed=7)
        b, _ = stratified_sample(pairs, 5, seed=7)
        c, _ = stratified_sample(pairs, 5, seed=8)
        assert [p.key() for p in a] == [p.key() for p in b]
        assert [p.key() for p in a] != [p.key() for p in c]

    def test_company_split_no_leak(self):
        pairs = [_pair(f"co{i}", f"co{i+1}", i % 11) for i in range(300)]
        train, held, dropped = split_by_company(pairs, held_frac=0.2, seed=1)
        train_companies = {p.a_id for p in train} | {p.b_id for p in train}
        held_companies = {p.a_id for p in held} | {p.b_id for p in held}
        # train·held 회사 집합이 완전히 분리돼야 한다 (누수 0)
        assert train_companies.isdisjoint(held_companies)
        assert held and train                       # 둘 다 비지 않음
        assert len(train) + len(held) + len(dropped) == len(pairs)   # 보존

    def test_company_split_drops_crossing(self):
        # 교차 쌍(한쪽만 held)은 폐기돼야 — 체인 페어에서 반드시 발생
        pairs = [_pair(f"co{i}", f"co{i+1}", i % 11) for i in range(300)]
        _, _, dropped = split_by_company(pairs, held_frac=0.2, seed=1)
        assert len(dropped) > 0

    def test_histogram(self):
        h = histogram([_pair("a", "b", 3), _pair("c", "d", 3), _pair("e", "f", 7)])
        assert h[3] == 2 and h[7] == 1 and h[0] == 0
