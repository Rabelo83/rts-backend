from datetime import datetime

from flask import Flask

import routes.map_api as map_api
from routes.map_api import map_bp


def _app():
    app = Flask(__name__)
    app.register_blueprint(map_bp)
    return app


def test_map_stop_schedule_returns_formatted_departures(monkeypatch):
    def fake_schedule(text, stop_id=None):
        assert text == "now"
        assert stop_id == "0001"
        return {
            "stop": "Rosa Parks RTS Downtown Station",
            "date": "2026-04-30",
            "time": "09:00:00",
            "next_by_route": [
                ("1", "09:25:00", "To Downtown Station"),
                ("11", "10:00:00", "To Eastwood Meadows"),
            ],
        }

    monkeypatch.setattr("routes.map_api.schedule_service.get_schedule_all_routes", fake_schedule)

    res = _app().test_client().get("/api/map/stop/1/schedule")

    assert res.status_code == 200
    data = res.get_json()
    assert data["stop_id"] == "0001"
    assert data["stop_name"] == "Rosa Parks RTS Downtown Station"
    assert data["source"] == "gtfs_schedule"
    assert data["service_day_label"] == "Today"
    assert data["departures"] == [
        {
            "route": "1",
            "time": "09:25:00",
            "time_label": "9:25 AM",
            "headsign": "To Downtown Station",
            "is_scheduled": True,
        },
        {
            "route": "11",
            "time": "10:00:00",
            "time_label": "10:00 AM",
            "headsign": "To Eastwood Meadows",
            "is_scheduled": True,
        },
    ]


def test_find_next_stop_schedule_rolls_forward_to_tomorrow(monkeypatch):
    class FakeDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 5, 1, 22, 30, 0)

    calls = []

    def fake_schedule(text, stop_id=None):
        calls.append((text, stop_id))
        if text == "now":
            return {
                "stop": "CVS @ NW 13th Street",
                "date": "2026-05-01",
                "time": "22:30:00",
                "next_by_route": [],
            }
        return {
            "stop": "CVS @ NW 13th Street",
            "date": "2026-05-02",
            "time": "00:00:00",
            "next_by_route": [
                ("8", "06:15:00", "To UF Health"),
            ],
        }

    monkeypatch.setattr("routes.map_api.schedule_service.get_schedule_all_routes", fake_schedule)
    monkeypatch.setattr(map_api, "datetime", FakeDateTime)

    data = map_api._find_next_stop_schedule("0369", limit=6)

    assert calls[0] == ("now", "0369")
    assert calls[1] == ("2026-05-02 midnight", "0369")
    assert data["service_day_label"] == "Tomorrow"
    assert data["departures"][0]["route"] == "8"
    assert data["departures"][0]["time_label"] == "6:15 AM"


def test_map_stop_schedule_rejects_invalid_stop_id():
    res = _app().test_client().get("/api/map/stop/abc/schedule")

    assert res.status_code == 400
    assert res.get_json() == {"error": "invalid_stop_id"}


def test_map_route_overview_returns_route_summary(monkeypatch):
    def fake_summary(route_id):
        assert route_id == "8"
        return {
            "route_id": "8",
            "route_long_name": "UF Health To N Walmart Supercenter",
            "date_iso": "2026-04-30",
            "day_label": "Thursday (weekday)",
            "runs_today": True,
            "directions": [
                {
                    "headsign": "To UF Health",
                    "first": "6:00 AM",
                    "last": "8:50 PM",
                    "frequency": "every ~55 min",
                    "trips": 17,
                },
            ],
        }

    def fake_service_hours(route_id):
        assert route_id == "8"
        return {
            "Weekday": {"first": "6:00 AM", "last": "8:50 PM"},
            "Saturday": {"first": "6:50 AM", "last": "6:00 PM"},
        }

    monkeypatch.setattr("routes.map_api.schedule_service.get_route_day_summary", fake_summary)
    monkeypatch.setattr("routes.map_api.schedule_service.get_route_first_last_by_service_type", fake_service_hours)

    res = _app().test_client().get("/api/map/route/8/overview")

    assert res.status_code == 200
    data = res.get_json()
    assert data["route"] == "8"
    assert data["route_name"] == "UF Health To N Walmart Supercenter"
    assert data["day_label"] == "Thursday (weekday)"
    assert data["runs_today"] is True
    assert data["directions"][0]["headsign"] == "To UF Health"
    assert data["schedule_by_service_type"]["Saturday"]["last"] == "6:00 PM"


def test_map_vehicles_surfaces_bustime_limit(monkeypatch):
    monkeypatch.setattr(map_api, "_vehicle_cache", None)
    monkeypatch.setattr(map_api, "_vehicle_cache_at", 0)

    def fake_fetch():
        raise map_api.BustimeVehicleError("Transaction limit for current day has been exceeded.")

    monkeypatch.setattr(map_api, "_fetch_all_vehicles", fake_fetch)

    res = _app().test_client().get("/api/map/vehicles")

    assert res.status_code == 200
    data = res.get_json()
    assert data["vehicles"] == []
    assert data["realtime_status"] == "limit_exceeded"
    assert "limit" in data["realtime_message"].lower()


def test_fetch_all_vehicles_keeps_batch_vehicles_when_one_route_has_no_data(monkeypatch):
    monkeypatch.setattr(map_api, "_routes_cache", [
        {"route_id": "1"},
        {"route_id": "6"},
    ])

    def fake_get_vehicles(route_ids):
        assert route_ids == "1,6"
        return {
            "vehicle": [
                {
                    "vid": "1502",
                    "lat": "29.645546",
                    "lon": "-82.322725",
                    "hdg": "359",
                    "spd": 0,
                    "rt": "1",
                    "des": "Butler Plaza Transfer Station",
                    "dly": False,
                    "tmstmp": "20260502 08:58",
                }
            ],
            "error": [
                {
                    "rtpidatafeed": "bustime",
                    "rt": "6",
                    "msg": "No data found for parameter",
                }
            ],
        }

    monkeypatch.setattr(map_api.rts_api, "get_vehicles", fake_get_vehicles)

    vehicles = map_api._fetch_all_vehicles()

    assert len(vehicles) == 1
    assert vehicles[0]["vehicle_id"] == "1502"
    assert vehicles[0]["route"] == "1"
