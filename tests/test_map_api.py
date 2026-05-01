from flask import Flask

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


def test_map_stop_schedule_rejects_invalid_stop_id():
    res = _app().test_client().get("/api/map/stop/abc/schedule")

    assert res.status_code == 400
    assert res.get_json() == {"error": "invalid_stop_id"}
