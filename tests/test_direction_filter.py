"""
Regression tests for _filter_inbound_departures in routes/agent_tools.py.

Real-world bug (2026-04-23): user asked "what time will the next 5 leave
Oaks Mall?" and the agent returned a 3:24 PM bus whose headsign was
"To Oaks Mall" — i.e. an ARRIVING bus, not a departing one. Root cause
was the direction filter's single-direction substring check missing the
common case where GTFS stop names are longer than headsigns.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from routes.agent_tools import _filter_inbound_departures


def _dep(headsign: str, time: str = "3:24 PM") -> dict:
    return {"headsign": headsign, "time": time}


class TestDirectionFilter:
    def test_long_stop_name_filters_matching_headsign(self):
        # The production bug: stop name is the verbose GTFS form,
        # headsign is the short form.
        deps = [
            _dep("To Oaks Mall"),         # arriving — must be filtered
            _dep("To Downtown Station"),  # departing — must remain
        ]
        out = _filter_inbound_departures(deps, "Oaks Mall SW 62nd Blvd")
        assert len(out) == 1
        assert out[0]["headsign"] == "To Downtown Station"

    def test_abbreviated_headsign_still_matches_via_fallback(self):
        # Headsign says "TS" (abbreviation); stop spells out "Transfer Station".
        # First-two-words fallback should still catch it.
        deps = [
            _dep("To Butler Plaza TS"),
            _dep("To Rosa Parks"),
        ]
        out = _filter_inbound_departures(deps, "Butler Plaza Transfer Station")
        headsigns = [d["headsign"] for d in out]
        assert "To Butler Plaza TS" not in headsigns
        assert "To Rosa Parks" in headsigns

    def test_short_stop_name_still_works(self):
        # Old code path (stop name shorter than headsign) must not regress.
        deps = [
            _dep("To Oaks Mall"),
            _dep("To Reitz Union"),
        ]
        out = _filter_inbound_departures(deps, "Oaks Mall")
        headsigns = [d["headsign"] for d in out]
        assert "To Oaks Mall" not in headsigns
        assert "To Reitz Union" in headsigns

    def test_unrelated_headsign_is_not_filtered(self):
        # Destination keyword doesn't appear in stop name — keep it.
        deps = [_dep("To Downtown")]
        out = _filter_inbound_departures(deps, "Oaks Mall SW 62nd Blvd")
        assert len(out) == 1

    def test_fallback_returns_original_when_all_filtered(self):
        # If filtering would return empty, fall back to original list.
        deps = [_dep("To Oaks Mall")]
        out = _filter_inbound_departures(deps, "Oaks Mall SW 62nd Blvd")
        assert out == deps  # never return empty

    def test_spanish_hacia_prefix(self):
        # Spanish headsigns use "Hacia" instead of "To".
        deps = [
            _dep("Hacia Oaks Mall"),
            _dep("Hacia Centro"),
        ]
        out = _filter_inbound_departures(deps, "Oaks Mall SW 62nd Blvd")
        headsigns = [d["headsign"] for d in out]
        assert "Hacia Oaks Mall" not in headsigns
        assert "Hacia Centro" in headsigns
