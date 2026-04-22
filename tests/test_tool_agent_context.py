"""
Tests for deterministic tool-agent follow-up context handling.
Run with: pytest tests/test_tool_agent_context.py -v
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from routes.agent_tools import dispatch_tool
from routes.tool_agent_context import (
    extract_context_updates,
    is_stop_id_followup,
    maybe_answer_stop_id_followup,
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
