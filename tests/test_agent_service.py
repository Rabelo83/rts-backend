"""
Tests for routes/agent_service.py — regex extraction, keyword detection,
transit gate, and context helpers.
Run with: pytest tests/test_agent_service.py -v
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from routes.agent_service import (
    extract_route_id_regex,
    extract_stop_id_regex,
    is_transit_keywords,
    wants_schedule,
    wants_realtime,
    has_explicit_timeframe,
    normalize_stop_id,
    guess_destination_hint,
    detect_language_simple,
    _has_strong_context,
    _last_user_with_context,
)


# ──────────────────────────────────────────────
# extract_route_id_regex
# ──────────────────────────────────────────────

class TestExtractRouteId:
    def test_route_number(self):
        assert extract_route_id_regex("route 43") == "43"

    def test_rt_abbreviation(self):
        assert extract_route_id_regex("rt 5") == "5"

    def test_bus_prefix(self):
        assert extract_route_id_regex("bus 75") == "75"

    def test_bus_number(self):
        assert extract_route_id_regex("bus number 12") == "12"

    def test_case_insensitive(self):
        assert extract_route_id_regex("Route 43") == "43"
        assert extract_route_id_regex("ROUTE 43") == "43"

    def test_in_sentence(self):
        assert extract_route_id_regex("when is the next route 43?") == "43"

    def test_santa_fe_query(self):
        assert extract_route_id_regex("when the next 43 will be at santa fe?") is None
        # "43" alone without prefix is not a route — requires "route/rt/bus"

    def test_no_route(self):
        assert extract_route_id_regex("what time does the bus stop?") is None

    def test_none_input(self):
        assert extract_route_id_regex(None) is None

    def test_empty_string(self):
        assert extract_route_id_regex("") is None


# ──────────────────────────────────────────────
# extract_stop_id_regex
# ──────────────────────────────────────────────

class TestExtractStopId:
    def test_stop_number(self):
        assert extract_stop_id_regex("stop 473") == "0473"

    def test_stop_id_keyword(self):
        assert extract_stop_id_regex("stop id 1001") == "1001"

    def test_hash_prefix(self):
        assert extract_stop_id_regex("#473") == "0473"

    def test_at_heuristic(self):
        assert extract_stop_id_regex("arrive at 1612") == "1612"

    def test_short_stop_gets_padded(self):
        assert extract_stop_id_regex("stop 73") == "0073"

    def test_no_stop(self):
        assert extract_stop_id_regex("route 43 santa fe") is None

    def test_none_input(self):
        assert extract_stop_id_regex(None) is None


# ──────────────────────────────────────────────
# normalize_stop_id
# ──────────────────────────────────────────────

class TestNormalizeStopId:
    def test_four_digit_passthrough(self):
        assert normalize_stop_id("1001") == "1001"

    def test_pads_short_id(self):
        assert normalize_stop_id("73") == "0073"

    def test_strips_leading_zeros_then_repads(self):
        # "0073" → int 73 → "0073"
        assert normalize_stop_id("0073") == "0073"

    def test_none_returns_none(self):
        assert normalize_stop_id(None) is None

    def test_non_numeric_returns_none(self):
        assert normalize_stop_id("abc") is None


# ──────────────────────────────────────────────
# is_transit_keywords  (the transit gate)
# ──────────────────────────────────────────────

class TestIsTransitKeywords:
    # Must pass
    def test_next_bus(self):
        assert is_transit_keywords("next bus") is True

    def test_when(self):
        assert is_transit_keywords("when does it leave?") is True

    def test_schedule(self):
        assert is_transit_keywords("show me the schedule") is True

    def test_route_word(self):
        assert is_transit_keywords("route 43") is True

    def test_stop_word(self):
        assert is_transit_keywords("stop 473") is True

    def test_spanish_horario(self):
        assert is_transit_keywords("cuál es el horario?") is True

    def test_spanish_parada(self):
        assert is_transit_keywords("parada 473") is True

    def test_eta(self):
        assert is_transit_keywords("eta") is True

    def test_depart(self):
        assert is_transit_keywords("when does it depart?") is True

    # Must fail
    def test_random_sentence(self):
        assert is_transit_keywords("hello, how are you?") is False

    def test_weather_query(self):
        assert is_transit_keywords("what's the weather today?") is False

    def test_empty(self):
        assert is_transit_keywords("") is False

    def test_none(self):
        assert is_transit_keywords(None) is False

    # Regression: "santa fe" query that was previously blocked
    def test_natural_language_with_next(self):
        assert is_transit_keywords("when the next 43 will be at santa fe?") is True


# ──────────────────────────────────────────────
# wants_schedule / wants_realtime
# ──────────────────────────────────────────────

class TestWantsScheduleRealtime:
    def test_schedule_keyword(self):
        assert wants_schedule("show me the schedule") is True

    def test_tomorrow_implies_schedule(self):
        assert wants_schedule("what buses run tomorrow?") is True

    def test_timetable(self):
        assert wants_schedule("timetable for route 5") is True

    def test_first_bus(self):
        assert wants_schedule("what's the first bus on route 43?") is True

    def test_realtime_eta(self):
        assert wants_realtime("what's the eta?") is True

    def test_realtime_minutes(self):
        assert wants_realtime("how many minutes?") is True

    def test_realtime_next_bus(self):
        assert wants_realtime("next bus") is True

    def test_realtime_spanish(self):
        assert wants_realtime("cuántos minutos?") is True


# ──────────────────────────────────────────────
# has_explicit_timeframe
# ──────────────────────────────────────────────

class TestHasExplicitTimeframe:
    def test_am_time(self):
        assert has_explicit_timeframe("at 7am") is True

    def test_pm_time(self):
        assert has_explicit_timeframe("3:30pm") is True

    def test_tomorrow(self):
        assert has_explicit_timeframe("tomorrow") is True

    def test_morning(self):
        assert has_explicit_timeframe("in the morning") is True

    def test_monday(self):
        assert has_explicit_timeframe("on monday") is True

    def test_noon(self):
        assert has_explicit_timeframe("at noon") is True

    def test_iso_date(self):
        assert has_explicit_timeframe("2026-03-15") is True

    def test_no_timeframe(self):
        assert has_explicit_timeframe("route 43 santa fe") is False

    def test_none(self):
        assert has_explicit_timeframe(None) is False


# ──────────────────────────────────────────────
# guess_destination_hint
# ──────────────────────────────────────────────

class TestGuessDestinationHint:
    def test_reitz(self):
        assert guess_destination_hint("bus to reitz") == "Reitz"

    def test_oaks(self):
        assert guess_destination_hint("from the oaks mall") == "Oaks"

    def test_downtown(self):
        assert guess_destination_hint("heading downtown") == "Downtown"

    def test_uf(self):
        assert guess_destination_hint("near uf campus") == "UF"

    def test_rosa_parks(self):
        assert guess_destination_hint("rosa parks transit center") == "Rosa Parks"

    def test_no_match(self):
        assert guess_destination_hint("santa fe college") is None

    def test_none(self):
        assert guess_destination_hint(None) is None


# ──────────────────────────────────────────────
# detect_language_simple
# ──────────────────────────────────────────────

class TestDetectLanguage:
    def test_english_default(self):
        assert detect_language_simple("when is the next bus?") == "en"

    def test_spanish_hola(self):
        assert detect_language_simple("hola, cuándo llega el bus?") == "es"

    def test_spanish_horario(self):
        assert detect_language_simple("horario de ruta 43") == "es"

    def test_none(self):
        assert detect_language_simple(None) == "en"


# ──────────────────────────────────────────────
# _has_strong_context
# ──────────────────────────────────────────────

class TestHasStrongContext:
    def test_route_number(self):
        assert _has_strong_context("route 43 tomorrow") is True

    def test_stop_number(self):
        assert _has_strong_context("stop 473") is True

    def test_explicit_time(self):
        assert _has_strong_context("after 7am") is True

    def test_from_keyword(self):
        assert _has_strong_context("leaving from oaks") is True

    def test_weak(self):
        assert _has_strong_context("hello") is False

    def test_empty(self):
        assert _has_strong_context("") is False


# ──────────────────────────────────────────────
# _last_user_with_context
# ──────────────────────────────────────────────

class TestLastUserWithContext:
    def _history(self, *msgs):
        result = []
        for i, m in enumerate(msgs):
            role = "user" if i % 2 == 0 else "assistant"
            result.append({"role": role, "content": m})
        return result

    def test_finds_message_with_timeframe(self):
        h = self._history(
            "route 43 tomorrow morning from santa fe",
            "Here are the times...",
            "stop 1001",
        )
        result = _last_user_with_context(h)
        # "stop 1001" has no context; should skip back to the first user message
        # which has "from" keyword
        assert "santa fe" in result or "tomorrow" in result

    def test_empty_history_returns_empty(self):
        assert _last_user_with_context([]) == ""

    def test_none_history_returns_empty(self):
        assert _last_user_with_context(None) == ""

    def test_no_context_message_returns_empty(self):
        h = self._history("hi", "hello!", "ok", "sure")
        assert _last_user_with_context(h) == ""
