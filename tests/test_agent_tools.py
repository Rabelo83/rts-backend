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
