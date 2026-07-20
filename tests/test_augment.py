"""증강 파이프라인 — 정직성 계약 테스트.

제1계율: 라벨-입력 일관성. 모든 증강 산출물에서 (a) 라벨이 입력에서 결정적으로
재유도 가능하거나 (b) 치환이 입력·라벨 양쪽에 동시 적용되었음을 검증한다.
"""
import json
import random

from app.augment import (augment, check_no_leak, entity_substitution,
                         field_dropout, fingerprint, llm_paraphrase,
                         shuffle_lines, synthesize)
from app.dataset import to_sft


def _label(ex):
    return json.loads(next(m for m in ex["messages"]
                           if m["role"] == "assistant")["content"])


def _user(ex):
    return next(m for m in ex["messages"] if m["role"] == "user")["content"]


class TestSynthesize:
    def test_deterministic(self):
        assert synthesize(30, seed=7) == synthesize(30, seed=7)
        assert synthesize(30, seed=7) != synthesize(30, seed=8)

    def test_labels_rederivable(self):
        """합성 라벨 = 엔진 파서의 결정적 출력 — 입력을 다시 태우면 같아야 한다."""
        from app.engine.represent import _mock_extract
        for ex in synthesize(20, seed=42):
            profile, oq = _mock_extract(_user(ex))
            label = _label(ex)
            assert label["profile"] == profile.model_dump(mode="json")
            assert label["open_questions"] == oq

    def test_diversity_no_duplicates(self):
        sources = [_user(ex) for ex in synthesize(100, seed=1)]
        assert len(set(sources)) == len(sources)


class TestEntitySubstitution:
    def test_consistent_both_sides(self):
        ex = synthesize(1, seed=3)[0]
        old = ex["meta"]["subject"]
        new = entity_substitution(ex, random.Random(0))
        assert new is not None
        assert old not in _user(new)                       # 입력에서 소거
        assert old not in json.dumps(_label(new), ensure_ascii=False)  # 라벨에서도
        assert new["meta"]["subject"] in _user(new)        # 새 이름이 실재
        assert new["meta"]["parent"] == fingerprint(ex)

    def test_label_still_valid(self):
        """치환 후에도 라벨은 입력의 결정적 재유도와 일치해야 한다."""
        from app.engine.represent import _mock_extract
        ex = entity_substitution(synthesize(1, seed=5)[0], random.Random(1))
        profile, _ = _mock_extract(_user(ex))
        assert _label(ex)["profile"] == profile.model_dump(mode="json")


class TestShuffleAndDropout:
    def test_shuffle_keeps_label(self):
        ex = synthesize(1, seed=11)[0]
        new = shuffle_lines(ex, random.Random(0))
        assert new is not None
        assert _label(new) == _label(ex)                   # 라벨 불변
        assert sorted(_user(new).splitlines()) == sorted(_user(ex).splitlines())
        assert _user(new).splitlines()[0].startswith("이름:")   # 주체 라인 고정

    def test_dropout_rederives_label(self):
        """필드를 떨어뜨리면 라벨도 그에 맞게 줄어야 한다 — 손대신 재유도."""
        from app.engine.represent import _mock_extract
        for seed in range(6):
            ex = synthesize(1, seed=seed)[0]
            new = field_dropout(ex, random.Random(seed))
            if new is None:              # 선택 필드가 없던 조합
                continue
            assert len(_user(new).splitlines()) < len(_user(ex).splitlines())
            profile, oq = _mock_extract(_user(new))
            assert _label(new)["profile"] == profile.model_dump(mode="json")
            assert _label(new)["open_questions"] == oq
            return
        raise AssertionError("드롭아웃 표본을 하나도 못 만들었다")

    def test_dropout_refuses_expert_labels(self):
        """전문가 라벨(trajectory)엔 재유도 불가 — 거부해야 정직하다."""
        ex = synthesize(1, seed=2)[0]
        ex["meta"]["label_source"] = "trajectory"
        assert field_dropout(ex, random.Random(0)) is None


class TestParaphraseGate:
    def test_gate_rejects_ungrounded(self):
        """패러프레이즈가 stated 근거를 지우면 폐기 — 오염 증강 차단."""
        ex = synthesize(1, seed=9)[0]
        bad_client = lambda text: "전혀 무관한 회사 소개 문장입니다. 근거가 없습니다."
        assert llm_paraphrase(ex, bad_client, random.Random(0)) is None

    def test_gate_accepts_grounded(self):
        """원문을 보존하는 패러프레이즈(라인 재배치+살 붙임)는 통과."""
        ex = synthesize(1, seed=9)[0]
        ok_client = lambda text: text + "\n비고: 위 내용은 회사 소개 자료 요약본이다."
        new = llm_paraphrase(ex, ok_client, random.Random(0))
        assert new is not None
        assert new["meta"]["strategy"] == "llm_paraphrase"

    def test_no_client_skips(self):
        assert llm_paraphrase(synthesize(1, seed=9)[0], None,
                              random.Random(0)) is None


class TestPipeline:
    def test_deterministic_and_deduped(self):
        base = synthesize(10, seed=42)
        a, ta = augment(base, factor=4, seed=42)
        b, _ = augment(base, factor=4, seed=42)
        assert a == b                                       # 재현 가능
        fps = [fingerprint(e) for e in a]
        assert len(set(fps)) == len(fps)                    # 완전 중복 없음
        assert ta["originals"] == 10
        assert ta["generated"] >= 10                        # 실제로 늘었다

    def test_augmented_tagged(self):
        out, _ = augment(synthesize(5, seed=1), factor=3, seed=1)
        for ex in out[5:]:
            assert ex["meta"]["augmented"] is True
            assert ex["meta"]["strategy"] in {"entity_substitution",
                                              "shuffle_lines", "field_dropout"}

    def test_seal_leak_detected(self):
        out, _ = augment(synthesize(3, seed=4), factor=2, seed=4)
        subject = out[0]["meta"]["subject"]
        assert check_no_leak(out, {"subjects": [subject]})   # 위반 감지
        assert not check_no_leak(out, {"subjects": ["없는회사"]})


class TestSftConversion:
    def test_represent_roundtrip(self):
        """입력 캡처된 represent 궤적 → SFT 쌍. 캡처 없는 구버전은 정직하게 skip."""
        rec = {"kind": "represent", "name": "테스트사", "engine_mode": "mock",
               "assets": ["text"], "open_questions": ["질문?"],
               "input_text": "이름: 테스트사\n문제: p",
               "profile_json": {"basic": {"name": "테스트사"}}}
        legacy = {"kind": "represent", "name": "옛날사", "engine_mode": "mock",
                  "assets": ["text"], "open_questions": []}
        examples, skipped = to_sft([rec, legacy])
        assert len(examples) == 1
        assert skipped["no_input"] == 1
        msgs = examples[0]["messages"]
        assert [m["role"] for m in msgs] == ["system", "user", "assistant"]
        assert "테스트사" in msgs[1]["content"]
        assert json.loads(msgs[2]["content"])["open_questions"] == ["질문?"]

    def test_judge_pair(self):
        rec = {"kind": "judge", "self": "갑", "counterpart": "을",
               "vantage": "seller", "objective": "exploration_budget",
               "decision": "conditional", "verdicts": {}, "trajectory": "...",
               "input_text": "렌즈(vantage): seller ...",
               "result_json": {"decision": "conditional"}}
        examples, skipped = to_sft([rec])
        assert len(examples) == 1
        assert examples[0]["meta"]["kind"] == "judge"
        assert skipped["no_input"] == 0
