"""
Tests for routes/schedule_service.py — parse_date, parse_time, and timetable.
Run with: pytest tests/test_schedule_service.py -v
"""
import sys
from pathlib import Path
from datetime import date, timedelta

# Make sure the project root is importable
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import routes.schedule_service as schedule_service
from routes.schedule_service import parse_date, parse_time, _select_key_stops


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


def test_get_route_departure_schedule_groups_departures(monkeypatch):
    class FakeCursor:
        def __init__(self, one=None, rows=None):
            self._one = one
            self._rows = rows or []

        def fetchone(self):
            return self._one

        def fetchall(self):
            return self._rows

    class FakeConn:
        def execute(self, sql, params=None):
            sql_compact = " ".join(sql.split())
            if "SELECT route_short_name, route_long_name FROM routes WHERE route_short_name = ?" in sql_compact:
                return FakeCursor(one={
                    "route_short_name": "10",
                    "route_long_name": "Downtown Station To Santa Fe College",
                })
            if "SELECT t.trip_headsign, s.stop_name AS origin_stop_name, st.departure_time" in sql_compact:
                return FakeCursor(rows=[
                    {
                        "trip_headsign": "To Santa Fe College",
                        "origin_stop_name": "Rosa Parks RTS Downtown Station",
                        "departure_time": "07:00:00",
                    },
                    {
                        "trip_headsign": "To Santa Fe College",
                        "origin_stop_name": "Rosa Parks RTS Downtown Station",
                        "departure_time": "08:00:00",
                    },
                    {
                        "trip_headsign": "To Downtown Station",
                        "origin_stop_name": "Santa Fe",
                        "departure_time": "07:30:00",
                    },
                ])
            raise AssertionError(f"Unexpected SQL in test: {sql_compact}")

        def close(self):
            return None

    monkeypatch.setattr(schedule_service, "connect_db", lambda: FakeConn())
    monkeypatch.setattr(
        schedule_service,
        "_resolve_schedule_target_date",
        lambda date_str=None: date(2026, 6, 2),
    )

    data = schedule_service.get_route_departure_schedule("10")

    assert data is not None
    assert data["route_id"] == "10"
    assert data["date_iso"] == "2026-06-02"
    assert data["total_departures"] == 3
    assert len(data["directions"]) == 2
    assert data["directions"][0]["headsign"] == "To Santa Fe College"
    assert data["directions"][0]["departures"][0]["time_label"] == "7:00 AM"
    assert data["directions"][1]["origin_stop_name"] == "Santa Fe"


# ──────────────────────────────────────────────
# _select_key_stops
# ──────────────────────────────────────────────

class TestSelectKeyStops:
    def _stops(self, n):
        return [{"stop_id_padded": str(i), "stop_name": f"Stop {i}"} for i in range(n)]

    def test_fewer_than_max_returns_all(self):
        stops = self._stops(5)
        result = _select_key_stops(stops, max_stops=8)
        assert result == stops

    def test_exactly_max_returns_all(self):
        stops = self._stops(8)
        result = _select_key_stops(stops, max_stops=8)
        assert len(result) == 8

    def test_always_includes_first_and_last(self):
        stops = self._stops(20)
        result = _select_key_stops(stops, max_stops=8)
        assert result[0] == stops[0]
        assert result[-1] == stops[-1]

    def test_returns_correct_count(self):
        stops = self._stops(30)
        result = _select_key_stops(stops, max_stops=8)
        assert len(result) == 8

    def test_max_two(self):
        stops = self._stops(10)
        result = _select_key_stops(stops, max_stops=2)
        assert len(result) == 2
        assert result[0] == stops[0]
        assert result[-1] == stops[-1]

    def test_single_stop(self):
        stops = self._stops(1)
        result = _select_key_stops(stops, max_stops=8)
        assert result == stops

    def test_ordered(self):
        stops = self._stops(20)
        result = _select_key_stops(stops, max_stops=6)
        indices = [stops.index(s) for s in result]
        assert indices == sorted(indices)


# ──────────────────────────────────────────────
# get_route_timetable
# ──────────────────────────────────────────────

class TestGetRouteTimetable:
    """Tests for get_route_timetable() using a monkeypatched DB connection."""

    def _fake_conn(self, service_ids=("Weekday",), direction="To Butler Plaza"):
        """Return a fake connection object that answers the queries in get_route_timetable."""
        import sqlite3

        class Row(dict):
            """Dict that also supports attribute-style access (like sqlite3.Row)."""
            def __getitem__(self, key):
                return super().__getitem__(key)
            def keys(self):
                return super().keys()

        class FakeCursor:
            def __init__(self, rows=None, one=None):
                self._rows = [Row(r) for r in (rows or [])]
                self._one  = Row(one) if one else None
            def fetchall(self):  return self._rows
            def fetchone(self):  return self._one

        call_log = []

        class FakeConn:
            def execute(self, sql, params=None):
                sql_c = " ".join(sql.split())
                call_log.append(sql_c[:60])

                # Route lookup
                if "route_short_name, route_long_name FROM routes WHERE route_short_name" in sql_c:
                    return FakeCursor(one={"route_short_name": "1", "route_long_name": "Downtown to Butler"})

                # All service_ids for route
                if "SELECT DISTINCT t.service_id FROM trips" in sql_c:
                    return FakeCursor(rows=[{"service_id": sid} for sid in service_ids])

                # Available directions
                if "SELECT DISTINCT t.trip_headsign FROM trips" in sql_c:
                    return FakeCursor(rows=[{"trip_headsign": direction}])

                # Representative trip
                if "SELECT t.trip_id, COUNT(st.stop_id) AS stop_count" in sql_c:
                    return FakeCursor(one={"trip_id": "T001", "stop_count": 5})

                # Stops for representative trip
                if "SELECT s.stop_id_padded, s.stop_name FROM stop_times st" in sql_c:
                    return FakeCursor(rows=[
                        {"stop_id_padded": "0001", "stop_name": "Stop A"},
                        {"stop_id_padded": "0002", "stop_name": "Stop B"},
                        {"stop_id_padded": "0003", "stop_name": "Stop C"},
                    ])

                # All trips ordered by first departure
                if "trip_first_seq" in sql_c and "SELECT t.trip_id, st.departure_time AS first_dep" in sql_c:
                    return FakeCursor(rows=[
                        {"trip_id": "T001", "first_dep": "06:30:00"},
                        {"trip_id": "T002", "first_dep": "07:00:00"},
                    ])

                # Times for all trips × key stops
                if "SELECT st.trip_id, s.stop_id_padded, st.departure_time FROM stop_times" in sql_c:
                    return FakeCursor(rows=[
                        {"trip_id": "T001", "stop_id_padded": "0001", "departure_time": "06:30:00"},
                        {"trip_id": "T001", "stop_id_padded": "0002", "departure_time": "06:40:00"},
                        {"trip_id": "T001", "stop_id_padded": "0003", "departure_time": "07:00:00"},
                        {"trip_id": "T002", "stop_id_padded": "0001", "departure_time": "07:00:00"},
                        {"trip_id": "T002", "stop_id_padded": "0002", "departure_time": "07:10:00"},
                        {"trip_id": "T002", "stop_id_padded": "0003", "departure_time": "07:30:00"},
                    ])

                raise AssertionError(f"Unexpected SQL in test: {sql_c[:80]}")

            def close(self):
                pass

        return FakeConn(), call_log

    def test_returns_correct_shape(self, monkeypatch):
        conn, _ = self._fake_conn()
        monkeypatch.setattr(schedule_service, "connect_db", lambda: conn)
        data = schedule_service.get_route_timetable("1", "weekday")
        assert data is not None
        assert data["route"] == "1"
        assert data["service_type"] == "weekday"
        assert data["service_label"] == "Weekday"

    def test_stops_and_rows(self, monkeypatch):
        conn, _ = self._fake_conn()
        monkeypatch.setattr(schedule_service, "connect_db", lambda: conn)
        data = schedule_service.get_route_timetable("1", "weekday")
        assert len(data["stops"]) == 3
        assert len(data["rows"]) == 2
        assert data["rows"][0]["times"][0] == "6:30 AM"
        assert data["rows"][1]["times"][-1] == "7:30 AM"

    def test_available_service_types_includes_weekday(self, monkeypatch):
        conn, _ = self._fake_conn(service_ids=("Weekday", "Saturday"))
        monkeypatch.setattr(schedule_service, "connect_db", lambda: conn)
        data = schedule_service.get_route_timetable("1", "weekday")
        assert "weekday" in data["available_service_types"]
        assert "saturday" in data["available_service_types"]

    def test_returns_none_for_missing_db(self, monkeypatch):
        monkeypatch.setattr(schedule_service, "connect_db", lambda: None)
        assert schedule_service.get_route_timetable("1") is None

    def test_returns_none_for_unknown_route(self, monkeypatch):
        class FakeConn:
            def execute(self, sql, params=None):
                class FC:
                    def fetchone(self): return None
                    def fetchall(self): return []
                return FC()
            def close(self): pass

        monkeypatch.setattr(schedule_service, "connect_db", lambda: FakeConn())
        assert schedule_service.get_route_timetable("999") is None

    def test_empty_rows_when_no_service(self, monkeypatch):
        """Service type requested but no matching service_ids in DB → empty timetable."""
        conn, _ = self._fake_conn(service_ids=("Weekday",))
        monkeypatch.setattr(schedule_service, "connect_db", lambda: conn)
        data = schedule_service.get_route_timetable("1", "saturday")
        assert data is not None
        assert data["rows"] == []
        assert data["stops"] == []
