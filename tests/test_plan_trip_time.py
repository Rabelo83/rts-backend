"""
Tests for plan_trip time-constraint plumbing.
Run with: pytest tests/test_plan_trip_time.py -v
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from routes.agent_tools import TOOLS, _to_hhmm, _tool_plan_trip


class TestToHHMM:
    def test_parses_natural_forms(self):
        assert _to_hhmm("2pm") == "14:00"
        assert _to_hhmm("2:30 PM") == "14:30"
        assert _to_hhmm("14:00") == "14:00"
        assert _to_hhmm("2 am") == "02:00"
        assert _to_hhmm("") is None
        assert _to_hhmm("now") is None


class TestPlanTripTimeConstraints:
    def test_tool_plan_trip_passes_arrive_by_through(self, monkeypatch):
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
        assert captured["depart_after"] is None

    def test_arrive_by_wins_when_both_are_set(self, monkeypatch):
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
            depart_at="8am",
            arrive_by="2pm",
        )

        assert captured["arrive_by"] == "14:00"
        assert captured["depart_after"] is None


class TestPlanTripSchema:
    def test_schema_advertises_new_fields(self):
        plan_trip = next(
            tool for tool in TOOLS if tool["function"]["name"] == "plan_trip"
        )
        props = plan_trip["function"]["parameters"]["properties"]
        required = plan_trip["function"]["parameters"].get("required", [])

        assert "depart_at" in props
        assert "arrive_by" in props
        assert "depart_at" not in required
        assert "arrive_by" not in required
