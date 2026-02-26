"""
Tests for routes/schedule_service.py — parse_date and parse_time.
Run with: pytest tests/test_schedule_service.py -v
"""
import sys
from pathlib import Path
from datetime import date, timedelta

# Make sure the project root is importable
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from routes.schedule_service import parse_date, parse_time


# ──────────────────────────────────────────────
# parse_time
# ──────────────────────────────────────────────

class TestParseTime:
    # Explicit times — must win over vague words
    def test_explicit_am(self):
        assert parse_time("7am") == "07:00:00"

    def test_explicit_pm(self):
        assert parse_time("3pm") == "15:00:00"

    def test_explicit_with_minutes(self):
        assert parse_time("3:30pm") == "15:30:00"

    def test_explicit_12am_midnight(self):
        assert parse_time("12am") == "00:00:00"

    def test_explicit_12pm_noon(self):
        assert parse_time("12pm") == "12:00:00"

    def test_explicit_beats_morning_in_context(self):
        # Regression: "after 7am? tomorrow morning bus 5" must return 7am, not 6am
        assert parse_time("after 7am? tomorrow morning bus 5") == "07:00:00"

    def test_explicit_beats_afternoon_in_context(self):
        assert parse_time("what about 2pm, in the afternoon?") == "14:00:00"

    def test_explicit_with_space_before_am(self):
        assert parse_time("9 am") == "09:00:00"

    def test_explicit_single_digit_minute(self):
        # e.g. "8:5am" edge case — zfill pads to "05"
        assert parse_time("8:5am") == "08:05:00"

    # Vague time-of-day words (only when no explicit time present)
    def test_morning(self):
        assert parse_time("tomorrow morning") == "06:00:00"

    def test_manana_spanish(self):
        assert parse_time("mañana") == "06:00:00"

    def test_afternoon(self):
        assert parse_time("this afternoon") == "12:00:00"

    def test_tarde_spanish(self):
        assert parse_time("por la tarde") == "12:00:00"

    def test_evening(self):
        assert parse_time("in the evening") == "17:00:00"

    def test_night(self):
        assert parse_time("tonight") == "17:00:00"

    def test_noche_spanish(self):
        assert parse_time("de noche") == "17:00:00"

    # Named anchors
    def test_noon(self):
        assert parse_time("around noon") == "12:00:00"

    def test_midnight(self):
        assert parse_time("after midnight") == "00:00:00"

    # Edge / empty cases
    def test_none_input(self):
        assert parse_time(None) is None

    def test_empty_string(self):
        assert parse_time("") is None

    def test_no_time_returns_none(self):
        assert parse_time("route 43 santa fe") is None


# ──────────────────────────────────────────────
# parse_date
# ──────────────────────────────────────────────

class TestParseDate:
    def test_today(self):
        assert parse_date("today") == date.today()

    def test_tomorrow(self):
        assert parse_date("tomorrow") == date.today() + timedelta(days=1)

    def test_iso_format(self):
        assert parse_date("2026-03-15") == date(2026, 3, 15)

    def test_us_slash_format(self):
        assert parse_date("03/15/2026") == date(2026, 3, 15)

    def test_none_returns_today(self):
        assert parse_date(None) == date.today()

    def test_empty_returns_today(self):
        assert parse_date("") == date.today()

    def test_no_date_keyword_returns_today(self):
        assert parse_date("route 43 bus") == date.today()

    def test_weekday_saturday(self):
        result = parse_date("saturday")
        # Result must be a Saturday
        assert result.weekday() == 5

    def test_weekday_monday(self):
        result = parse_date("monday")
        assert result.weekday() == 0

    def test_weekday_today_same_day(self):
        today = date.today()
        day_name = today.strftime("%A").lower()
        result = parse_date(day_name)
        # When asking for today's weekday name, returns today (days_ahead == 0)
        assert result == today

    def test_weekend_keyword(self):
        result = parse_date("this weekend")
        assert result.weekday() >= 5  # Sat or Sun

    def test_weekday_keyword(self):
        result = parse_date("weekdays")
        assert result.weekday() < 5  # Mon–Fri

    def test_spanish_fin_de_semana(self):
        result = parse_date("fin de semana")
        assert result.weekday() >= 5

    def test_spanish_dias_de_semana(self):
        result = parse_date("dias de semana")
        assert result.weekday() < 5

    def test_case_insensitive(self):
        assert parse_date("TOMORROW") == date.today() + timedelta(days=1)
        assert parse_date("Today") == date.today()
