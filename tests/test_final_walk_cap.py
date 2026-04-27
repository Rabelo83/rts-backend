"""
Regression test for the final-walk cap in utils/trip_planner.py.

Production bug (2026-04-23): "Plan a Trip" from Rosa Parks to 224 SE 24th St
returned an option that ended with a 17-minute walk from "Bartley Temple
Methodist Church" to the destination. Users saw it as "Route 26 To Airport"
going to the wrong place. Root cause: the stop-finder's 5 km fallback
produced distant stops; find_trips() surfaced them without a final-walk
cap.

This test pins _MAX_FINAL_WALK_MIN so the cap can't silently regress.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from utils import trip_planner


def _itin(walk_from_min: float, total_min: int = 20, route: str = "26") -> dict:
    return {
        "total_min": total_min,
        "walk_to_stop": {"walk_min": 2},
        "walk_from_stop": {"walk_min": walk_from_min},
        "legs": [
            {"type": "bus", "route": route, "depart_min": 600, "headsign": "To Airport"},
        ],
        "same_side": False,
        "realtime": False,
    }


class TestFinalWalkCap:
    def test_drops_itineraries_exceeding_cap(self):
        # Walk-from 17 min is the exact bug case. Should be dropped.
        good = _itin(walk_from_min=5,  route="7")
        bad  = _itin(walk_from_min=17, route="26")
        out = trip_planner._dedup_and_rank([good, bad])
        routes = {l["route"] for itin in out for l in itin["legs"] if l["type"] == "bus"}
        assert "7" in routes
        assert "26" not in routes

    def test_keeps_itineraries_at_cap_boundary(self):
        # Exactly _MAX_FINAL_WALK_MIN is allowed.
        boundary = _itin(walk_from_min=trip_planner._MAX_FINAL_WALK_MIN)
        out = trip_planner._dedup_and_rank([boundary])
        assert len(out) == 1

    def test_all_filtered_returns_empty(self):
        # If every candidate exceeds the cap, return empty — the downstream
        # code already handles no_routes gracefully.
        too_far_1 = _itin(walk_from_min=17, route="26")
        too_far_2 = _itin(walk_from_min=20, route="8")
        out = trip_planner._dedup_and_rank([too_far_1, too_far_2])
        assert out == []

    def test_missing_walk_from_stop_treated_as_zero(self):
        # Defensive: if an itinerary has no walk_from_stop key, don't crash.
        weird = {
            "total_min": 10,
            "walk_to_stop": {"walk_min": 2},
            "legs": [{"type": "bus", "route": "1", "depart_min": 100, "headsign": "Downtown"}],
            "same_side": False,
            "realtime": False,
        }
        out = trip_planner._dedup_and_rank([weird])
        assert len(out) == 1

    def test_cap_constant_is_reasonable(self):
        # Lock the cap at a user-sensible value — 10 <= cap <= 15 min.
        assert 10 <= trip_planner._MAX_FINAL_WALK_MIN <= 15
