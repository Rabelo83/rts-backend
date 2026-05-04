"""
Tests for route-aware stop search in routes/agent_tools.py.
Run with: pytest tests/test_agent_tools.py -v
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from routes.agent_tools import dispatch_tool


class TestSearchStopsRouteAware:
    def test_oaks_mall_route_5_auto_selects_only_valid_stop(self):
        result = dispatch_tool("search_stops", {"name": "Oaks Mall", "route_id": "5"})

        assert result["status"] == "found"
        assert result["stop_id"] == "0175"
        assert "Oaks Mall" in result["stop_name"]

    def test_oaks_mall_without_route_stays_ambiguous(self):
        result = dispatch_tool("search_stops", {"name": "Oaks Mall"})

        assert result["status"] == "multiple"
        stop_ids = {c["stop_id"] for c in result["candidates"]}
        assert {"0171", "0172", "0175", "1097"}.issubset(stop_ids)

    def test_shands_route_43_includes_uf_health_alias_stops(self):
        result = dispatch_tool("search_stops", {"name": "Shands", "route_id": "43"})

        assert result["status"] == "multiple"
        stop_ids = {c["stop_id"] for c in result["candidates"]}
        assert {"0042", "0446", "0664"}.issubset(stop_ids)


class TestScheduleRouteAwareAliases:
    def test_route_43_schedule_from_shands_returns_route_scoped_candidates(self):
        result = dispatch_tool(
            "get_schedule",
            {"route_id": "43", "stop_name": "Shands", "kind": "next", "time": "3pm"},
        )

        assert result["status"] == "multiple_stops"
        stop_ids = {c["stop_id_padded"] for c in result["candidates"]}
        assert {"0042", "0446", "0664"}.issubset(stop_ids)


class TestRealtimeRouteFilter:
    def test_realtime_predictions_can_filter_to_specific_route(self, monkeypatch):
        from routes import agent_tools

        monkeypatch.setattr(agent_tools, "get_predictions_cached", lambda stop_id: {
            "prd": [
                {"rt": "7", "des": "Eastwood Meadows", "prdctdn": "DUE", "stpnm": "Rosa Parks Downtown Station"},
                {"rt": "1", "des": "Butler Plaza Transfer Station", "prdctdn": "28", "stpnm": "Rosa Parks Downtown Station"},
            ]
        })

        result = dispatch_tool("get_realtime_predictions", {"stop_id": "0001", "route_id": "1"})

        assert result["status"] == "ok"
        assert result["route_filter"] == "1"
        assert result["predictions"] == [
            {"route": "1", "headsign": "Butler Plaza Transfer Station", "minutes": 28, "delayed": False}
        ]

    def test_realtime_predictions_reports_missing_route_without_gtfs_claim(self, monkeypatch):
        from routes import agent_tools

        monkeypatch.setattr(agent_tools, "get_predictions_cached", lambda stop_id: {
            "prd": [
                {"rt": "7", "des": "Eastwood Meadows", "prdctdn": "DUE", "stpnm": "Rosa Parks Downtown Station"},
                {"rt": "26", "des": "GNV Airport", "prdctdn": "26", "stpnm": "Rosa Parks Downtown Station"},
            ]
        })

        result = dispatch_tool("get_realtime_predictions", {"stop_id": "0001", "route_id": "1"})

        assert result["status"] == "no_route_prediction"
        assert result["route"] == "1"
        assert result["available_routes"] == ["26", "7"]
