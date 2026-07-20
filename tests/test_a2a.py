"""A2A 전송 계층 테스트 — JSON-RPC 2.0 + SSE 스트리밍.

conftest가 Mock 모드를 강제하므로 represent는 빠르게 완결된다(외부 API 없음).
JSON-RPC 봉투·에러코드·Task lifecycle·SSE 이벤트 순서를 실제 엔드포인트로 검증한다.
"""
import json
import time

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

# 최소 프로필 4필드가 다 찬 자료 — represent가 게이트를 통과해 completed까지 간다
DECK_TEXT = ("이름: 다이브인그룹\n국가: 한국\n산업: hospitality\n설명: 노후 호텔 전환\n"
             "문제: 노후 객실 매출 정체\n솔루션: 저자본 예술 전환\n"
             "타겟: 중소 호텔 오너\n판매가치: 매출")


def _rpc(method, params, req_id="1"):
    return client.post("/a2a", json={
        "jsonrpc": "2.0", "id": req_id, "method": method, "params": params})


def _represent_msg(text=DECK_TEXT):
    return {"message": {"role": "user", "kind": "message", "messageId": "m",
            "parts": [{"kind": "data", "data": {
                "skill": "represent",
                "input": {"assets": [{"type": "text", "content": text}]}}}]}}


def _wait_terminal(task_id, timeout=15):
    for _ in range(int(timeout / 0.2)):
        body = _rpc("tasks/get", {"id": task_id}).json()["result"]
        if body["status"]["state"] in ("completed", "failed", "input-required",
                                       "canceled"):
            return body
        time.sleep(0.2)
    raise AssertionError("Task가 종료되지 않음")


class TestJsonRpcEnvelope:
    def test_parse_error_minus_32700(self):
        res = client.post("/a2a", content=b"not json{{",
                          headers={"Content-Type": "application/json"})
        assert res.json()["error"]["code"] == -32700

    def test_invalid_request_minus_32600(self):
        # jsonrpc 버전 누락
        res = client.post("/a2a", json={"id": "1", "method": "tasks/get"})
        assert res.json()["error"]["code"] == -32600

    def test_method_not_found_minus_32601(self):
        assert _rpc("foo/bar", {}).json()["error"]["code"] == -32601

    def test_invalid_params_unknown_skill_minus_32602(self):
        params = {"message": {"role": "user", "kind": "message", "messageId": "m",
                  "parts": [{"kind": "data", "data": {"skill": "nope", "input": {}}}]}}
        err = _rpc("message/send", params).json()["error"]
        assert err["code"] == -32602 and "nope" in err["message"]

    def test_invalid_params_no_skill_part(self):
        params = {"message": {"role": "user", "kind": "message", "messageId": "m",
                  "parts": [{"kind": "text", "text": "안녕"}]}}
        assert _rpc("message/send", params).json()["error"]["code"] == -32602

    def test_id_is_echoed(self):
        res = _rpc("message/send", _represent_msg(), req_id="echo-42")
        assert res.json()["id"] == "echo-42"


class TestTaskLifecycle:
    def test_send_returns_task_object(self):
        result = _rpc("message/send", _represent_msg()).json()["result"]
        assert result["kind"] == "task"
        assert result["status"]["state"] in ("submitted", "working")
        assert result["metadata"]["skill"] == "represent"
        # A2A history — 보낸 메시지가 Task.history에 보존된다
        assert result["history"][0]["parts"][0]["data"]["skill"] == "represent"

    def test_get_reaches_completed_with_artifact(self):
        task_id = _rpc("message/send", _represent_msg()).json()["result"]["id"]
        final = _wait_terminal(task_id)
        assert final["status"]["state"] == "completed"
        assert final["artifacts"][0]["artifactId"] == "art-represent"
        profile = final["artifacts"][0]["parts"][0]["data"]["profile"]
        assert profile["basic"]["name"] == "다이브인그룹"

    def test_get_unknown_task_minus_32001(self):
        assert _rpc("tasks/get", {"id": "없는id"}).json()["error"]["code"] == -32001

    def test_cancel_terminal_task_not_cancelable_minus_32002(self):
        task_id = _rpc("message/send", _represent_msg()).json()["result"]["id"]
        _wait_terminal(task_id)
        err = _rpc("tasks/cancel", {"id": task_id}).json()["error"]
        assert err["code"] == -32002

    def test_input_required_when_below_minimum(self):
        """최소 프로필 미달 → EngineError(409) → A2A input-required로 매핑."""
        task_id = _rpc("message/send",
                       _represent_msg("이름: 빈회사")).json()["result"]["id"]
        final = _wait_terminal(task_id)
        assert final["status"]["state"] == "input-required"


class TestSseStreaming:
    def test_stream_emits_task_then_updates_then_artifact_then_final(self):
        body = {"jsonrpc": "2.0", "id": "stream-1", "method": "message/stream",
                "params": _represent_msg()}
        events = []
        with client.stream("POST", "/a2a", json=body) as res:
            assert res.headers["content-type"].startswith("text/event-stream")
            for line in res.iter_lines():
                if line.startswith("data: "):
                    events.append(json.loads(line[6:])["result"])

        kinds = [e.get("kind") for e in events]
        assert kinds[0] == "task"                       # 최초 Task 스냅샷
        assert "status-update" in kinds                 # 진행 이벤트
        assert "artifact-update" in kinds               # 산출물
        # 마지막은 final=true status-update(completed)
        assert events[-1]["kind"] == "status-update"
        assert events[-1]["final"] is True
        assert events[-1]["status"]["state"] == "completed"
        # 모든 이벤트가 같은 JSON-RPC id를 단다
        # (id는 봉투에 있으므로 여기선 event 내부 taskId 일관성으로 확인)
        task_ids = {e.get("taskId") for e in events if e.get("kind") != "task"}
        assert len(task_ids) == 1

    def test_stream_validation_error_before_stream(self):
        """스킬 검증 실패는 스트림 시작 전에 JSON-RPC 에러로 반환(SSE 아님)."""
        body = {"jsonrpc": "2.0", "id": "x", "method": "message/stream",
                "params": {"message": {"role": "user", "kind": "message",
                           "messageId": "m", "parts": [{"kind": "data",
                           "data": {"skill": "nope", "input": {}}}]}}}
        res = client.post("/a2a", json=body)
        assert res.json()["error"]["code"] == -32602


class TestAgentCardTransport:
    def test_card_advertises_jsonrpc_and_streaming(self):
        card = client.get("/.well-known/agent.json").json()
        assert card["capabilities"]["streaming"] is True
        assert card["preferredTransport"] == "JSONRPC"
        assert any(i["transport"] == "JSONRPC" and i["url"].endswith("/a2a")
                   for i in card["additionalInterfaces"])
