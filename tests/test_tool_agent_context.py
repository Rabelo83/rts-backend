"""
Tests for deterministic tool-agent follow-up context handling.
Run with: pytest tests/test_tool_agent_context.py -v
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from routes.agent_tools import dispatch_tool
from routes.tool_agent_context import (
    add_stop_id_to_answer,
    extract_context_updates,
    is_stop_id_followup,
    maybe_answer_stop_id_followup,
    maybe_rewrite_route_stop_followup,
)


class TestStopIdFollowups:
    def test_generic_stop_id_followup_uses_saved_context(self):
        result = maybe_answer_stop_id_followup(
            "what will be the stop id?",
            {
                "context": {
                    "last_stop_id": "0175",
                    "last_stop_name": "Oaks Mall SW 62nd Blvd",
                    "last_route_id": "5",
                }
            },
            "en",
        )

        assert result is not None
        assert result["answer"] == "The stop ID for Oaks Mall SW 62nd Blvd is 0175."
        assert result["meta"]["stop_id"] == "0175"

    def test_explicit_new_place_does_not_reuse_old_context(self):
        assert is_stop_id_followup("what is the stop id for reitz union?") is False
        result = maybe_answer_stop_id_followup(
            "what is the stop id for reitz union?",
            {"context": {"last_stop_id": "0175", "last_stop_name": "Oaks Mall SW 62nd Blvd"}},
            "en",
        )
        assert result is None


class TestContextExtraction:
    def test_route_aware_stop_search_updates_context(self):
        tool_result = dispatch_tool("search_stops", {"name": "Oaks Mall", "route_id": "5"})
        updates = extract_context_updates([{"tool": "search_stops", "result": tool_result}])

        assert updates["last_stop_id"] == "0175"
        assert updates["last_stop_name"] == "Oaks Mall SW 62nd Blvd"
        assert updates["last_route_id"] == "5"


class TestRouteStopFollowups:
    def test_stop_id_reply_keeps_route_context_when_assistant_asked_for_stop(self):
        rewritten = maybe_rewrite_route_stop_followup(
            "stop 0446",
            [
                {"role": "user", "content": "when the next 43 will leave shands?"},
                {
                    "role": "assistant",
                    "content": "Route 43 does not appear to serve Shands Hospital @ Center Drive. Would you like to know when Route 43 runs from a different stop?",
                },
            ],
            {"context": {"last_route_id": "43"}},
        )

        assert rewritten == "route 43 stop 0446"


class TestAnswerEnrichment:
    def test_resolved_stop_answer_gets_stop_id_suffix(self):
        tool_result = dispatch_tool("search_stops", {"name": "Oaks Mall", "route_id": "5"})
        answer = add_stop_id_to_answer(
            "The next Route 5 bus is arriving now at Oaks Mall SW 62nd Blvd.",
            [{"tool": "search_stops", "result": tool_result}],
            "en",
        )

        assert answer.endswith("Stop ID: 175.")

    def test_existing_stop_id_is_not_duplicated(self):
        tool_result = dispatch_tool("search_stops", {"name": "Oaks Mall", "route_id": "5"})
        answer = add_stop_id_to_answer(
            "The next Route 5 bus is arriving now at Oaks Mall SW 62nd Blvd (Stop 175).",
            [{"tool": "search_stops", "result": tool_result}],
            "en",
        )

        assert answer == "The next Route 5 bus is arriving now at Oaks Mall SW 62nd Blvd (Stop 175)."
