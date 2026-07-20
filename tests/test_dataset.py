"""Phase 4 CoT 데이터 파이프라인 (DAT-01~05) — 순수 함수, 오프라인.

audit JSONL을 임시로 만들어 검증·커버리지·결정적 분할·봉인 누수검사를 확인한다.
"""
import json

from app import dataset

# 정상 6 + 불량 3(필드누락·미지kind·파싱실패). 머쉬앤은 represent+judge 2궤적.
LINES = [
    '{"ts":"t","kind":"represent","name":"다이브인","engine_mode":"llm","assets":["ir_deck"],"open_questions":[]}',
    '{"ts":"t","kind":"represent","name":"머쉬앤","engine_mode":"mock","assets":["text"],"open_questions":["q"]}',
    '{"ts":"t","kind":"judge","self":"머쉬앤","counterpart":"X","vantage":"seller","objective":"exploration_budget","decision":"proceed","verdicts":{},"trajectory":"..."}',
    '{"ts":"t","kind":"negotiate","seller":"다이브인","buyer":"코봇","termination":"agreement","rounds_used":3,"rounds":[]}',
    '{"ts":"t","kind":"consult","company":"에이엔폴리","turn":1,"history":[],"output":{}}',
    '{"ts":"t","kind":"judge","self":"코봇","counterpart":"Y","vantage":"buyer","objective":"exploration_budget","decision":"hold","verdicts":{},"trajectory":"..."}',
    '{"ts":"t","kind":"judge","self":"머쉬앤"}',            # 필드 누락
    '{"ts":"t","kind":"mystery","foo":1}',                  # 미지 kind
    'not json at all',                                      # 파싱 실패
]


def _audit(tmp_path):
    (tmp_path / "20260707.jsonl").write_text("\n".join(LINES), encoding="utf-8")
    return dataset.load_records(tmp_path)


class TestValidation:
    def test_isolates_three_bad_records(self, tmp_path):
        report = dataset.validate_records(_audit(tmp_path))
        assert len(report.valid) == 6
        assert len(report.errors) == 3
        reasons = " ".join(e["reason"] for e in report.errors)
        assert "필수 필드 누락" in reasons
        assert "미지 kind" in reasons
        assert "파싱 실패" in reasons
        assert not report.ok


class TestCoverage:
    def test_counts_by_kind_and_dimension(self, tmp_path):
        valid = dataset.validate_records(_audit(tmp_path)).valid
        cov = dataset.coverage_matrix(valid)
        assert cov["by_kind"] == {"represent": 2, "judge": 2,
                                  "negotiate": 1, "consult": 1}
        assert cov["dimensions"]["judge.decision"] == {"proceed": 1, "hold": 1}
        assert cov["dimensions"]["represent.engine_mode"] == {"llm": 1, "mock": 1}


class TestDeterministicSplit:
    def test_split_is_reproducible(self, tmp_path):
        valid = dataset.validate_records(_audit(tmp_path)).valid
        t1, h1 = dataset.split_held_out(valid, 0.5)
        t2, h2 = dataset.split_held_out(valid, 0.5)
        assert [dataset._subject_key(r) for r in h1] == \
               [dataset._subject_key(r) for r in h2]

    def test_same_subject_never_splits(self, tmp_path):
        """머쉬앤의 represent+judge 두 궤적은 반드시 같은 쪽(누수 방지)."""
        valid = dataset.validate_records(_audit(tmp_path)).valid
        train, held = dataset.split_held_out(valid, 0.5)
        train_subj = {dataset._subject_key(r) for r in train}
        held_subj = {dataset._subject_key(r) for r in held}
        assert train_subj.isdisjoint(held_subj)          # 주체가 양쪽에 겹치지 않음


class TestSeal:
    def test_seal_and_verify_no_leak_on_clean_split(self, tmp_path):
        valid = dataset.validate_records(_audit(tmp_path)).valid
        train, held = dataset.split_held_out(valid, 0.5)
        assert dataset.verify_seal(train, dataset.seal(held)) == []

    def test_verify_catches_injected_leak(self, tmp_path):
        valid = dataset.validate_records(_audit(tmp_path)).valid
        train, held = dataset.split_held_out(valid, 0.5)
        sealed = dataset.seal(held)
        # held-out 레코드를 train에 억지로 섞으면 누수로 잡혀야 한다
        leaked = train + [held[0]]
        violations = dataset.verify_seal(leaked, sealed)
        assert violations and ("누수" in violations[0])


class TestBuildArtifacts:
    def test_build_writes_all_artifacts(self, tmp_path):
        audit_dir = tmp_path / "audit"
        audit_dir.mkdir()
        (audit_dir / "20260707.jsonl").write_text("\n".join(LINES), encoding="utf-8")
        out = tmp_path / "dataset"
        summary = dataset.build(audit_dir, out, held_frac=0.4)

        assert summary["valid"] == 6 and summary["errors"] == 3
        assert summary["train"] + summary["heldout"] == 6
        assert summary["seal_violations"] == []
        for name in ("train.jsonl", "heldout.jsonl", "seal.json", "report.json"):
            assert (out / name).exists()
        # 산출 JSONL에는 내부 메타(_source/_line)가 새지 않아야 한다
        first = json.loads((out / "train.jsonl").read_text().splitlines()[0]) \
            if summary["train"] else {}
        assert not any(k.startswith("_") for k in first)
        assert json.loads((out / "seal.json").read_text())["sealed"] is True
