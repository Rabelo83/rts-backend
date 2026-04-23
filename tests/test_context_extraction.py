"""
Regression tests for Claude multi-turn context extraction and reuse.
Run with: pytest tests/test_context_extraction.py -v
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from routes.agent_claude import _build_last_known_block
from routes.tool_agent_context import (
    extract_context_updates,
    maybe_rewrite_route_stop_followup,
)


class TestContextExtraction:
    def test_schedule_extraction_captures_date_and_direction(self):
        tool_results_log = [
            {
                "tool": "get_schedule",
                "result": {
                    "status": "ok",
                    "route": "15",
                    "stop": "University Ave & 13th St",
                    "stop_id": "0221",
                    "date": "2026-04-24",
                    "departures": [
                        {"headsign": "Butler Plaza", "time": "5:10 PM"},
                        {"headsign": "Butler Plaza", "time": "5:25 PM"},
                    ],
                },
            }
        ]

        updates = extract_context_updates(tool_results_log)

        assert updates["last_route_id"] == "15"
        assert updates["last_stop_id"] == "0221"
        assert updates["last_stop_name"] == "University Ave & 13th St"
        assert updates["last_date"] == "2026-04-24"
        assert updates["last_direction"] == "Butler Plaza"
        assert updates["last_tool"] == "get_schedule"

    def test_single_vehicle_location_captures_stop_and_direction(self):
        tool_results_log = [
            {
                "tool": "get_vehicle_location",
                "result": {
                    "status": "ok",
                    "route": "8",
                    "vehicles": [
                        {
                            "next_stop_id": "0473",
                            "next_stop_name": "NW 13th & University",
                            "destination": "Butler Plaza",
                        }
                    ],
                },
            }
        ]

        updates = extract_context_updates(tool_results_log)

        assert updates["last_route_id"] == "8"
        assert updates["last_stop_id"] == "0473"
        assert updates["last_stop_name"] == "NW 13th & University"
        assert updates["last_direction"] == "Butler Plaza"
        assert updates["last_tool"] == "get_vehicle_location"


class TestRouteStopRewrite:
    def test_route_rewrite_uses_word_boundary(self):
        ctx = {"context": {"last_route_id": "1"}}

        stale_history = [
            {"role": "assistant", "content": "Route 15 stops at which stop id?"}
        ]
        assert maybe_rewrite_route_stop_followup("stop 221", stale_history, ctx) is None

        matching_history = [
            {"role": "assistant", "content": "Route 1 stops at which stop id?"}
        ]
        assert (
            maybe_rewrite_route_stop_followup("stop 221", matching_history, ctx)
            == "route 1 stop 221"
        )


class TestLastKnownBlock:
    def test_last_known_block_renders_only_present_fields(self):
        block = _build_last_known_block(
            {
                "last_route_id": "15",
                "last_stop_id": "0221",
                "last_stop_name": "University Ave & 13th St",
                "last_direction": "Butler Plaza",
            }
        )

        assert "LAST KNOWN CONTEXT (from prior turns in this conversation):" in block
        assert "route: 15" in block
        assert "stop: 0221 (University Ave & 13th St)" in block
        assert "direction: Butler Plaza" in block
        assert "date:" not in block
