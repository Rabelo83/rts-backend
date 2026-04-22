"""
tests/test_pwa_manifest.py

Verifies the PWA manifest route and service-worker route are correct.

Run:
    pytest tests/test_pwa_manifest.py -v
"""
import sys
from pathlib import Path
import pytest

# Ensure utils is on path for agency_config
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "utils"))


@pytest.fixture(scope="module")
def client():
    """Flask test client with the full app factory."""
    from app import create_app
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


# ── /manifest.json ───────────────────────────────────────────────────────────

def test_manifest_returns_200(client):
    r = client.get("/manifest.json")
    assert r.status_code == 200


def test_manifest_content_type(client):
    r = client.get("/manifest.json")
    assert "application/manifest+json" in r.content_type


def test_manifest_name_matches_agency_config(client):
    from agency_config import get_agency_full_name
    r = client.get("/manifest.json")
    data = r.get_json()
    assert data is not None, "manifest.json did not return valid JSON"
    assert data["name"] == f"{get_agency_full_name()} \u2014 Bus Tracker"


def test_manifest_short_name(client):
    from agency_config import get_agency_short_name
    r = client.get("/manifest.json")
    data = r.get_json()
    assert data["short_name"] == f"{get_agency_short_name()} Bus"


def test_manifest_has_required_fields(client):
    r = client.get("/manifest.json")
    data = r.get_json()
    for field in ("name", "short_name", "description", "start_url",
                  "display", "background_color", "theme_color",
                  "lang", "icons", "scope"):
        assert field in data, f"manifest.json missing field: {field}"


def test_manifest_has_two_icon_sizes(client):
    r = client.get("/manifest.json")
    data = r.get_json()
    sizes = {icon["sizes"] for icon in data.get("icons", [])}
    assert "192x192" in sizes
    assert "512x512" in sizes


def test_manifest_no_hardcoded_agency_strings(client):
    """
    The manifest must not contain raw 'Gainesville', 'go-rts', or 'RTS'
    as standalone literals — all values must come from the config template.
    We allow 'RTS' only inside the agency name (which came from the config).
    """
    import json
    from agency_config import get_agency_config
    r = client.get("/manifest.json")
    raw = r.data.decode()
    # The only allowed occurrences of the agency full_name come from config
    cfg_name = get_agency_config()["agency"]["full_name"]
    # Remove the config-sourced name, then check no stray hardcodes
    sanitised = raw.replace(cfg_name, "")
    assert "go-rts" not in sanitised.lower()


# ── /service-worker.js ───────────────────────────────────────────────────────

def test_service_worker_returns_200(client):
    r = client.get("/service-worker.js")
    assert r.status_code == 200


def test_service_worker_content_type(client):
    r = client.get("/service-worker.js")
    assert "javascript" in r.content_type


def test_service_worker_scope_header(client):
    r = client.get("/service-worker.js")
    assert r.headers.get("Service-Worker-Allowed") == "/"


def test_service_worker_has_push_handlers(client):
    """add-web-push is complete: push + notificationclick handlers must be present."""
    r = client.get("/service-worker.js")
    body = r.data.decode()
    assert "self.addEventListener('push'" in body
    assert "self.addEventListener('notificationclick'" in body


# ── /icons/* ─────────────────────────────────────────────────────────────────

def test_icon_192_served(client):
    r = client.get("/icons/icon-192.png")
    assert r.status_code == 200
    assert r.content_type == "image/png"


def test_icon_512_served(client):
    r = client.get("/icons/icon-512.png")
    assert r.status_code == 200


def test_apple_touch_icon_served(client):
    r = client.get("/icons/apple-touch-icon.png")
    assert r.status_code == 200
