"""메일 공개 본문과 내부 판단 근거의 분리 계약."""

from types import SimpleNamespace

import pytest

from app.engine.compose import _finalize_response, _public_body
from app.errors import EngineError
from app.product.router import _compose_reasoning_summary
from app.schemas import (ClaimTrace, ComposedMessage, ComposeResponse, Lens)


LEAKED_BODY = """변형 variant_A — 객실 매출 정체, 리뉴얼 부담으로 어려움을 겪고 계시다면
호안끼엠 호텔의 문제를 함께 검토할 수 있습니다. (fit_reason_ref: fit_reasons[1])
Hotel K 사례에서 공간 가치를 높였습니다. (reference: Hotel K ContentsArt)
다음 단계로 30분 미팅을 제안드립니다."""


def _request(reason_count=2):
    return SimpleNamespace(
        variants=2,
        lens=Lens.sell,
        judge_result=SimpleNamespace(
            fit_reasons=[f"근거 {i}" for i in range(reason_count)]),
    )


def test_public_body_removes_only_internal_trace_annotations():
    body = _public_body(
        LEAKED_BODY
        + "\n(reference_used: Hotel K ContentsArt)"
        + "\n일반 괄호(30분)는 유지합니다.")

    assert not body.startswith("변형")
    assert "fit_reason_ref" not in body
    assert "fit_reasons[" not in body
    assert "(reference:" not in body
    assert "reference_used" not in body
    assert "Hotel K 사례에서 공간 가치를 높였습니다." in body
    assert "일반 괄호(30분)는 유지합니다." in body


def test_public_body_removes_single_line_variant_prefix():
    body = _public_body("변형 variant_B — 담당자님께, 짧은 미팅을 제안드립니다.")

    assert body == "담당자님께, 짧은 미팅을 제안드립니다."


def test_finalize_normalizes_variants_and_validates_trace_indexes():
    messages = [
        ComposedMessage(
            variant_label="variant_A",
            title="제안 A",
            body=LEAKED_BODY,
            claim_trace=[
                ClaimTrace(claim="유효", fit_reason_ref="fit_reasons[1]"),
                ClaimTrace(claim="범위 밖", fit_reason_ref="fit_reasons[9]"),
            ],
            reference_used="Hotel K ContentsArt",
        ),
        ComposedMessage(
            variant_label="variant_B",
            title="제안 B",
            body="담당자님께,\n\n짧은 미팅을 제안드립니다.",
            claim_trace=[
                ClaimTrace(claim="유효", fit_reason_ref="fit_reasons[0]")],
            reference_used="first_case",
        ),
    ]

    response = _finalize_response(
        _request(), ComposeResponse(messages=messages, send_blocked=True))

    assert [message.variant_label for message in response.messages] == ["A", "B"]
    assert [trace.fit_reason_ref for trace in response.messages[0].claim_trace] == [
        "fit_reasons[1]"]
    assert response.send_blocked is True


def test_finalize_rejects_body_that_contains_only_internal_metadata():
    message = ComposedMessage(
        variant_label="A",
        title="빈 제안",
        body="(fit_reason_ref: fit_reasons[0])",
        claim_trace=[
            ClaimTrace(claim="근거", fit_reason_ref="fit_reasons[0]")],
        reference_used="first_case",
    )

    with pytest.raises(EngineError) as exc:
        _finalize_response(
            _request(1), ComposeResponse(messages=[message], send_blocked=True))

    assert exc.value.code == "compose_invalid_output"


def test_product_reasoning_summary_excludes_raw_trajectory():
    judge = SimpleNamespace(
        decision=SimpleNamespace(value="conditional"),
        decision_rationale="조건 확인 후 진행",
        fit_reasons=["공간 가치 상승"],
        gap_factors=["현장 설치 조건 미확인"],
        risks=[
            SimpleNamespace(model_dump=lambda mode: {
                "type": "precondition", "description": "설치 가능 여부 확인"})],
        match_summary=SimpleNamespace(reference="Hotel K ContentsArt"),
        deal_structure="1개 층 PoC",
        trajectory="외부에 노출하지 않을 원시 추론",
    )

    summary = _compose_reasoning_summary(judge)

    assert summary["fit_reasons"] == ["공간 가치 상승"]
    assert summary["reference"] == "Hotel K ContentsArt"
    assert "trajectory" not in summary
