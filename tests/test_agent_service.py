"""
Tests for transit agent helpers — regex extraction, keyword detection,
transit gate, and context helpers.
Run with: pytest tests/test_agent_service.py -v
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Parsing utilities now live in their own module
from routes.parsing_helpers import (
    extract_route_id_regex,
    extract_stop_id_regex,
    normalize_stop_id,
    is_transit_keywords,
    wants_schedule,
    wants_realtime,
    has_explicit_timeframe,
    guess_destination_hint,
    detect_language_simple,
    _has_strong_context,
    _is_followup_after,
    _extract_last_departure_time,
)

# History helpers stay in the orchestration module
from routes.agent_service import _last_user_with_context


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


# ──────────────────────────────────────────────
# _is_followup_after
# ──────────────────────────────────────────────

class TestIsFollowupAfter:
    def test_after_that(self):
        assert _is_followup_after("after that") is True

    def test_the_one_after_that(self):
        assert _is_followup_after("the one after that?") is True

    def test_what_about_after(self):
        assert _is_followup_after("what about after that?") is True

    def test_one_after_that(self):
        assert _is_followup_after("one after that") is True

    def test_next_after(self):
        assert _is_followup_after("next after that") is True

    def test_after_this_one(self):
        assert _is_followup_after("after this one?") is True

    def test_what_comes_after(self):
        assert _is_followup_after("what comes after?") is True

    # Must NOT match
    def test_after_7am(self):
        assert _is_followup_after("after 7am") is False

    def test_next_bus(self):
        assert _is_followup_after("next bus") is False

    def test_none(self):
        assert _is_followup_after(None) is False

    def test_empty(self):
        assert _is_followup_after("") is False

    # Regression: explicit times must NOT be treated as vague follow-ups
    def test_what_about_after_6pm(self):
        assert _is_followup_after("what about after 6pm?") is False

    def test_what_about_after_330pm(self):
        assert _is_followup_after("what about after 3:30pm?") is False

    def test_what_about_after_no_time(self):
        # No explicit time → IS a followup
        assert _is_followup_after("what about after that?") is True


# ──────────────────────────────────────────────
# _extract_last_departure_time
# ──────────────────────────────────────────────

class TestExtractLastDepartureTime:
    def test_single_pm_time(self):
        assert _extract_last_departure_time("The next bus is at 3:30 PM.") == "3:30pm"

    def test_single_am_time(self):
        assert _extract_last_departure_time("Departs at 7:00 AM from Rosa Parks.") == "7:00am"

    def test_multiple_times_returns_last(self):
        text = "- 3:00 PM (To NW 13th St)\n- 3:30 PM (To NW 13th St)\n- 4:00 PM (To NW 13th St)"
        assert _extract_last_departure_time(text) == "4:00pm"

    def test_case_insensitive(self):
        assert _extract_last_departure_time("Bus at 2:15 pm") == "2:15pm"

    def test_no_time_returns_none(self):
        assert _extract_last_departure_time("Sorry, no departures found.") is None

    def test_none_returns_none(self):
        assert _extract_last_departure_time(None) is None

    def test_empty_returns_none(self):
        assert _extract_last_departure_time("") is None


# ──────────────────────────────────────────────
# _advance_time_one_minute
# ──────────────────────────────────────────────

from routes.parsing_helpers import _advance_time_one_minute


class TestAdvanceTimeOneMinute:
    """Regression: 'after that?' should advance threshold so GTFS >= doesn't re-show same bus."""

    def test_basic_pm(self):
        assert _advance_time_one_minute("5:16pm") == "5:17pm"

    def test_basic_am(self):
        assert _advance_time_one_minute("7:00am") == "7:01am"

    def test_minute_rollover(self):
        # 59 → 00, hour increments
        assert _advance_time_one_minute("5:59pm") == "6:00pm"

    def test_hour_rollover_noon(self):
        # 11:59am → 12:00pm
        assert _advance_time_one_minute("11:59am") == "12:00pm"

    def test_hour_rollover_midnight(self):
        # 11:59pm → 12:00am
        assert _advance_time_one_minute("11:59pm") == "12:00am"

    def test_noon_12pm(self):
        # 12:00pm + 1 → 12:01pm
        assert _advance_time_one_minute("12:00pm") == "12:01pm"

    def test_midnight_12am(self):
        # 12:00am = 00:00 + 1 → 00:01 = 12:01am
        assert _advance_time_one_minute("12:00am") == "12:01am"

    def test_bad_input_passthrough(self):
        assert _advance_time_one_minute("not-a-time") == "not-a-time"

    def test_none_passthrough(self):
        assert _advance_time_one_minute(None) is None


# ──────────────────────────────────────────────
# Greeting detection in try_transit_answer
# ──────────────────────────────────────────────

from unittest.mock import patch


class TestGreetingDetection:
    """Greetings must NOT merge with prior transit history or return schedule results."""

    _TRANSIT_HISTORY = [
        {"role": "user", "content": "what is the schedule for route 43 tomorrow after 5pm"},
        {"role": "assistant", "content": "Next departures for route 43 after 5:00 PM:\n- 5:16 PM (To Downtown)"},
    ]

    def test_hi_returns_none(self):
        # try_transit_answer must return None for greetings (no transit content)
        from routes.agent_service import try_transit_answer
        result = try_transit_answer("hi", history=self._TRANSIT_HISTORY)
        assert result is None

    def test_hello_returns_none(self):
        from routes.agent_service import try_transit_answer
        result = try_transit_answer("hello", history=self._TRANSIT_HISTORY)
        assert result is None

    def test_hola_returns_none(self):
        from routes.agent_service import try_transit_answer
        result = try_transit_answer("hola", history=self._TRANSIT_HISTORY)
        assert result is None

    def test_hi_with_exclamation_returns_none(self):
        from routes.agent_service import try_transit_answer
        result = try_transit_answer("hi!", history=self._TRANSIT_HISTORY)
        assert result is None

    def test_hey_returns_none(self):
        from routes.agent_service import try_transit_answer
        result = try_transit_answer("hey", history=self._TRANSIT_HISTORY)
        assert result is None
