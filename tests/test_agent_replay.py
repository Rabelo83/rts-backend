"""
Replay-style regression tests for agent hardening work.
Run with: pytest tests/test_agent_replay.py -v
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import routes.agent_tools as agent_tools
import utils.geocoding as geocoding
from routes.agent_claude import _fallback_from_tool_results
from routes.agent_tools import _tool_plan_trip, dispatch_tool
from utils.geocoding import geocode


class TestAgentReplay:
    def test_arrive_by_passes_through_to_trip_planner(self, monkeypatch):
        captured = {}

        def fake_geocode(value):
            return {"lat": 29.0, "lon": -82.0, "formatted_address": value}

        def fake_find_trips(**kwargs):
            captured.update(kwargs)
            return {"itineraries": [], "service_label": "Weekday"}

        monkeypatch.setattr("utils.geocoding.geocode", fake_geocode)
        monkeypatch.setattr("utils.trip_planner.find_trips", fake_find_trips)

        _tool_plan_trip(
            origin="Rosa Parks",
            destination="Santa Fe College",
            arrive_by="2pm",
        )

        assert captured["arrive_by"] == "14:00"

    def test_landmark_lookup_bypasses_provider(self, monkeypatch):
        monkeypatch.setattr(geocoding, "PROVIDER", "google")
        monkeypatch.setattr(geocoding, "_LANDMARKS_LOADED", False)
        monkeypatch.setattr(geocoding, "_LANDMARKS", {})

        def boom(_query):
            raise AssertionError("provider should not be called for landmark shortcut")

        monkeypatch.setattr(geocoding, "_google_geocode", boom)

        result = geocode("Rosa Parks")

        assert result is not None
        assert result["formatted_address"] == "Rosa Parks RTS Downtown Station"
        assert result["lat"] == 29.6528
        assert result["lon"] == -82.3248

    def test_landmark_alias_resolution_is_case_insensitive(self, monkeypatch):
        monkeypatch.setattr(geocoding, "_LANDMARKS_LOADED", False)
        monkeypatch.setattr(geocoding, "_LANDMARKS", {})

        assert geocode("butler plaza") is not None
        assert geocode("BUTLER PLAZA") is not None
        assert geocode("the oaks mall") is not None

    def test_empty_text_fallback_surfaces_geocode_failed_message(self):
        text = _fallback_from_tool_results(
            [
                {
                    "tool": "plan_trip",
                    "result": {
                        "status": "geocode_failed",
                        "message": "Could not find 'nowhereville'.",
                    },
                }
            ],
            "en",
        )

        assert "nowhereville" in text

    def test_dispatch_tool_logs_session_id(self, monkeypatch):
        logged = []

        def fake_info(message, *args):
            logged.append(message % args)

        def fake_search_stops(**_kwargs):
            return {"status": "found", "stop_id": "0001", "stop_name": "Test Stop"}

        monkeypatch.setattr(agent_tools.logger, "info", fake_info)
        monkeypatch.setattr(agent_tools, "_tool_search_stops", fake_search_stops)

        dispatch_tool("search_stops", {"name": "test"}, session_id="abc-123")

        assert any("session=abc-123" in line for line in logged)
