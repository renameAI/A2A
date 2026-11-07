"""Claude 호출층(chat, web_research) 단위 테스트. 네트워크 없이 가짜 클라이언트만 쓴다."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import interview_test as agent


def text(value, citations=None):
    return NS(type="text", text=value, citations=citations)


class FakeStream:
    def __init__(self, message):
        self.message = message

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self.message


class FakeMessages:
    """create/stream 호출 인자를 기록하고 준비된 응답을 차례로 돌려준다."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def create(self, **params):
        self.calls.append(params)
        return self.responses.pop(0)

    def stream(self, **params):
        self.calls.append(params)
        return FakeStream(self.responses.pop(0))


def fake_client(responses):
    messages = FakeMessages(responses)
    return NS(messages=messages, beta=NS(messages=messages)), messages


class ChatTests(unittest.TestCase):
    def test_collects_text_blocks_after_thinking(self):
        response = NS(
            stop_reason="end_turn",
            content=[NS(type="thinking", thinking=""), text('{"a":'), text(" 1}")],
        )
        client, calls = fake_client([response])
        raw = agent.chat(client, "sys", [{"role": "user", "content": "q"}], max_tokens=400)
        self.assertEqual('{"a": 1}', raw)
        params = calls.calls[0]
        self.assertEqual(agent.ANTHROPIC_MODEL, params["model"])
        self.assertEqual("sys", params["system"])
        self.assertNotIn("temperature", params)
        self.assertEqual({"type": "adaptive"}, params["thinking"])
        self.assertEqual(400 + agent.THINKING_HEADROOM_TOKENS, params["max_tokens"])
        if agent.REFUSAL_FALLBACK:
            self.assertEqual("default", params["fallbacks"])

    def test_refusal_and_empty_truncation_raise(self):
        for response in (
            NS(stop_reason="refusal", content=[]),
            NS(stop_reason="max_tokens", content=[NS(type="thinking", thinking="")]),
        ):
            client, _ = fake_client([response])
            with self.assertRaises(RuntimeError):
                agent.chat(client, "sys", [{"role": "user", "content": "q"}])


class WebResearchTests(unittest.TestCase):
    def test_resumes_pause_turn_and_appends_sources(self):
        search_use = NS(type="server_tool_use", name="web_search", input={"query": "샘플호텔"})
        search_result = NS(
            type="web_search_tool_result",
            content=[NS(type="web_search_result", url="https://example.com/a")],
        )
        paused = NS(stop_reason="pause_turn", content=[search_use, search_result])
        done = NS(
            stop_reason="end_turn",
            content=[
                text("# 리서치\n제품은 "),
                text("ESG 캠페인이다.", citations=[NS(url="https://example.com/b")]),
            ],
        )
        client, calls = fake_client([paused, done])
        with patch.object(agent.time, "sleep"):
            doc = agent.web_research(client, "샘플호텔", "")
        self.assertIn("제품은 ESG 캠페인이다.", doc)
        self.assertIn("https://example.com/a", doc)
        self.assertIn("https://example.com/b", doc)
        self.assertEqual("web_search_20260209", calls.calls[0]["tools"][0]["type"])
        resumed = calls.calls[1]["messages"]
        self.assertEqual(["user", "assistant"], [m["role"] for m in resumed])
        self.assertEqual([search_use, search_result], resumed[1]["content"])

    def test_fails_without_search_evidence(self):
        no_search = NS(stop_reason="end_turn", content=[text("내부지식 문서")])
        client, _ = fake_client([no_search] * 3)
        with patch.object(agent.time, "sleep"), self.assertRaises(RuntimeError):
            agent.web_research(client, "샘플호텔", "")


if __name__ == "__main__":
    unittest.main()
