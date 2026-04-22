"""
tests/test_web_push.py

Web push + favorites test suite.

Run with:
    ENABLE_ALERT_SCHEDULER=false .tools/python311/bin/python3 -m pytest tests/test_web_push.py -v

All tests use an in-memory (or temp) push DB via monkeypatching.
"""

import os
import sys
import uuid
import sqlite3
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Disable scheduler in tests
os.environ["ENABLE_ALERT_SCHEDULER"] = "false"

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "utils"))


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def app():
    from app import create_app
    application = create_app()
    application.config["TESTING"] = True
    return application


@pytest.fixture(scope="module")
def client(app):
    with app.test_client() as c:
        yield c


@pytest.fixture()
def tmp_push_db(monkeypatch, tmp_path):
    """Replace get_push_db with a fresh in-memory connection per test."""
    schema = (Path(__file__).resolve().parents[1] / "db" / "push_schema.sql").read_text()
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(schema)

    import utils.push_db as push_db_mod
    monkeypatch.setattr(push_db_mod, "get_push_db", lambda: conn)

    # Also patch inside routes
    import routes.push as push_mod
    monkeypatch.setattr(push_mod, "get_push_db", lambda: conn)
    import routes.favorites as fav_mod
    monkeypatch.setattr(fav_mod, "get_push_db", lambda: conn)

    yield conn
    conn.close()


def _new_uuid():
    return str(uuid.uuid4())


# ── VAPID key endpoint ────────────────────────────────────────────────────────

def test_vapid_key_endpoint_503_when_not_set(client):
    """Without VAPID_PUBLIC_KEY env var the endpoint returns 503."""
    with patch.dict(os.environ, {"VAPID_PUBLIC_KEY": ""}):
        r = client.get("/api/push/vapid-public-key")
    assert r.status_code == 503


def test_vapid_key_endpoint_returns_key(client):
    """With a VAPID_PUBLIC_KEY set, endpoint returns 200 + key."""
    with patch.dict(os.environ, {"VAPID_PUBLIC_KEY": "test_fake_b64_key"}):
        r = client.get("/api/push/vapid-public-key")
    assert r.status_code == 200
    data = r.get_json()
    assert data["key"] == "test_fake_b64_key"


# ── Subscribe / Unsubscribe ───────────────────────────────────────────────────

def test_subscribe_creates_row(client, tmp_push_db):
    uid = _new_uuid()
    r = client.post("/api/push/subscribe", json={
        "anon_uuid": uid,
        "subscription": {
            "endpoint": "https://fcm.example.com/push/abc123",
            "keys": {"p256dh": "fake_p256dh", "auth": "fake_auth"},
        },
        "user_agent": "TestBrowser/1.0",
    })
    assert r.status_code == 204
    row = tmp_push_db.execute(
        "SELECT * FROM push_subscriptions WHERE anon_uuid=?", (uid,)
    ).fetchone()
    assert row is not None
    assert row["p256dh"] == "fake_p256dh"


def test_subscribe_invalid_uuid(client):
    r = client.post("/api/push/subscribe", json={
        "anon_uuid": "not-a-valid-uuid",
        "subscription": {
            "endpoint": "https://fcm.example.com/push/xyz",
            "keys": {"p256dh": "x", "auth": "y"},
        },
    })
    assert r.status_code == 400
    assert "invalid" in r.get_json().get("error", "").lower()


def test_subscribe_missing_fields(client):
    uid = _new_uuid()
    r = client.post("/api/push/subscribe", json={
        "anon_uuid": uid,
        "subscription": {"endpoint": "https://fcm.example.com/push/zzz"},
    })
    assert r.status_code == 400


def test_unsubscribe_removes_row(client, tmp_push_db):
    uid = _new_uuid()
    endpoint = "https://fcm.example.com/push/to-delete"
    client.post("/api/push/subscribe", json={
        "anon_uuid": uid,
        "subscription": {
            "endpoint": endpoint,
            "keys": {"p256dh": "x", "auth": "y"},
        },
    })
    r = client.delete("/api/push/unsubscribe", json={"endpoint": endpoint})
    assert r.status_code == 204
    row = tmp_push_db.execute(
        "SELECT * FROM push_subscriptions WHERE endpoint=?", (endpoint,)
    ).fetchone()
    assert row is None


# ── Identity ─────────────────────────────────────────────────────────────────

def test_identity_registration(client, tmp_push_db):
    uid = _new_uuid()
    r = client.post("/api/identity", json={"anon_uuid": uid, "language": "es"})
    assert r.status_code == 200
    assert r.get_json()["anon_uuid"] == uid
    row = tmp_push_db.execute(
        "SELECT language FROM user_identities WHERE anon_uuid=?", (uid,)
    ).fetchone()
    assert row["language"] == "es"


# ── Favorites CRUD ───────────────────────────────────────────────────────────

def test_favorite_create_and_list(client, tmp_push_db):
    uid = _new_uuid()
    r = client.post("/api/favorites", json={
        "anon_uuid": uid,
        "route_id": "20",
        "stop_id": "0173",
        "departure_hhmm": "07:30",
        "days_of_week": "mon,tue,wed,thu,fri",
        "delay_threshold_min": 5,
    })
    assert r.status_code == 201
    fav = r.get_json()
    assert fav["route_id"] == "20"
    assert fav["stop_id"] == "0173"

    r2 = client.get(f"/api/favorites?anon_uuid={uid}")
    assert r2.status_code == 200
    items = r2.get_json()
    assert len(items) == 1
    assert items[0]["id"] == fav["id"]


def test_favorite_patch_active(client, tmp_push_db):
    uid = _new_uuid()
    r = client.post("/api/favorites", json={
        "anon_uuid": uid, "route_id": "5", "stop_id": "0001",
        "departure_hhmm": "08:00", "days_of_week": "mon",
    })
    fav_id = r.get_json()["id"]
    r2 = client.patch(f"/api/favorites/{fav_id}", json={"anon_uuid": uid, "active": 0})
    assert r2.status_code == 200
    assert r2.get_json()["active"] == 0


def test_favorite_delete(client, tmp_push_db):
    uid = _new_uuid()
    r = client.post("/api/favorites", json={
        "anon_uuid": uid, "route_id": "11", "stop_id": "0555",
        "departure_hhmm": "09:00", "days_of_week": "fri",
    })
    fav_id = r.get_json()["id"]
    r2 = client.delete(f"/api/favorites/{fav_id}?anon_uuid={uid}")
    assert r2.status_code == 204
    r3 = client.get(f"/api/favorites?anon_uuid={uid}")
    assert r3.get_json() == []


def test_favorite_invalid_time_format(client):
    uid = _new_uuid()
    r = client.post("/api/favorites", json={
        "anon_uuid": uid, "route_id": "1", "stop_id": "1",
        "departure_hhmm": "25:99",  # invalid
        "days_of_week": "mon",
    })
    assert r.status_code == 400


# ── Scheduler dedupe ──────────────────────────────────────────────────────────

def test_alert_dedupe_logic(tmp_push_db):
    """Scheduler should skip if alert fired within last 30 min."""
    from utils.alert_scheduler import _was_recently_alerted

    uid = _new_uuid()
    tmp_push_db.execute("INSERT OR IGNORE INTO user_identities(anon_uuid) VALUES(?)", (uid,))
    tmp_push_db.execute(
        "INSERT INTO favorites(anon_uuid,route_id,stop_id,departure_hhmm,days_of_week,active) VALUES(?,?,?,?,?,1)",
        (uid, "20", "0173", "07:30", "mon"),
    )
    fav_id = tmp_push_db.execute("SELECT last_insert_rowid()").fetchone()[0]

    # Insert a recent 'sent' log entry
    recent = (datetime.utcnow() - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
    tmp_push_db.execute(
        "INSERT INTO alert_log(favorite_id, fired_at, delay_min, outcome) VALUES(?,?,?,?)",
        (fav_id, recent, 4, "sent"),
    )
    tmp_push_db.commit()

    assert _was_recently_alerted(tmp_push_db, fav_id, window_min=30) is True


def test_alert_dedupe_no_recent_alert(tmp_push_db):
    """Should NOT dedupe if last alert was > 30 min ago."""
    from utils.alert_scheduler import _was_recently_alerted

    uid = _new_uuid()
    tmp_push_db.execute("INSERT OR IGNORE INTO user_identities(anon_uuid) VALUES(?)", (uid,))
    tmp_push_db.execute(
        "INSERT INTO favorites(anon_uuid,route_id,stop_id,departure_hhmm,days_of_week,active) VALUES(?,?,?,?,?,1)",
        (uid, "75", "0999", "08:00", "tue"),
    )
    fav_id = tmp_push_db.execute("SELECT last_insert_rowid()").fetchone()[0]

    # Insert an OLD log entry
    old = (datetime.utcnow() - timedelta(minutes=60)).strftime("%Y-%m-%d %H:%M:%S")
    tmp_push_db.execute(
        "INSERT INTO alert_log(favorite_id, fired_at, delay_min, outcome) VALUES(?,?,?,?)",
        (fav_id, old, 3, "sent"),
    )
    tmp_push_db.commit()

    assert _was_recently_alerted(tmp_push_db, fav_id, window_min=30) is False


# ── Push payload templates ────────────────────────────────────────────────────

def test_push_payload_english():
    from utils.push_sender import build_push_payload
    p = build_push_payload("20", "0173", 6, "7:36 AM", lang="en")
    assert "20" in p["body"]
    assert "0173" in p["body"]
    assert "6" in p["body"]
    # No hardcoded agency-specific city name
    assert "Gainesville" not in p["body"]
    assert "go-rts" not in p["body"]


def test_push_payload_spanish():
    from utils.push_sender import build_push_payload
    p = build_push_payload("5", "0800", 3, "8:03 AM", lang="es")
    assert "5" in p["body"]
    assert "Gainesville" not in p["body"]
    # Spanish should have Spanish text
    assert any(word in p["body"] for word in ["tarde", "Ruta", "Parada"])


# ── VAPID subject from config ─────────────────────────────────────────────────

def test_vapid_subject_from_config():
    """get_vapid_subject() constructs mailto: from agency_config when env var not set."""
    with patch.dict(os.environ, {"VAPID_SUBJECT": ""}):
        from utils.agency_config import get_vapid_subject, get_agency_config
        # Clear lru_cache to re-read
        get_agency_config.cache_clear()
        subj = get_vapid_subject()
        assert subj.startswith("mailto:")
        assert "@" in subj


def test_vapid_subject_env_override():
    """VAPID_SUBJECT env var takes priority over config."""
    with patch.dict(os.environ, {"VAPID_SUBJECT": "mailto:custom@test.com"}):
        from utils.agency_config import get_vapid_subject
        subj = get_vapid_subject()
        assert subj == "mailto:custom@test.com"


# ── Scheduler disabled by env ─────────────────────────────────────────────────

def test_scheduler_disabled_by_env():
    """ENABLE_ALERT_SCHEDULER=false must return None from make_scheduler."""
    with patch.dict(os.environ, {"ENABLE_ALERT_SCHEDULER": "false"}):
        from utils.alert_scheduler import make_scheduler
        result = make_scheduler()
        assert result is None


# ── Push stats endpoint ───────────────────────────────────────────────────────

def test_push_stats_endpoint(client, tmp_push_db):
    """Push stats endpoint returns 200 with required keys."""
    r = client.get("/api/admin/push-stats")
    assert r.status_code == 200
    data = r.get_json()
    assert "active_subscriptions" in data
    assert "active_favorites" in data
    assert "alerts_sent_24h" in data
