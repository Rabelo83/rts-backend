"""
routes/pwa.py
Flask blueprint for Progressive Web App primitives.

Serves:
  GET /manifest.json         — dynamic Web App Manifest (reads agency_config.yaml)
  GET /service-worker.js     — service worker file with the required
                               Service-Worker-Allowed: / header so it can
                               control the whole origin (not just /static/).

No agency-specific strings are hardcoded here — everything is read from
agency_config.yaml via utils/agency_config.py.
"""
import os
import sys
from pathlib import Path
from flask import Blueprint, jsonify, send_from_directory, make_response

# Resolve utils path for direct execution
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "utils"))
from agency_config import (
    get_agency_config,
    get_agency_full_name,
    get_agency_short_name,
    get_primary_color,
    get_background_color,
    get_default_lang,
    get_city,
)

pwa_bp = Blueprint("pwa", __name__)

_PUBLIC_HTML = Path(__file__).resolve().parents[1] / "public_html"


@pwa_bp.route("/manifest.json")
def manifest():
    """
    Dynamic Web App Manifest.
    Values come from agency_config.yaml — no hardcodes.
    """
    short_name = get_agency_short_name()
    full_name = get_agency_full_name()
    city = get_city()

    data = {
        "name": f"{full_name} \u2014 Bus Tracker",
        "short_name": f"{short_name} Bus",
        "description": f"AI-powered transit assistant for {city} riders.",
        "start_url": "/chat",
        "display": "standalone",
        "orientation": "portrait",
        "background_color": get_background_color(),
        "theme_color": get_primary_color(),
        "lang": get_default_lang(),
        "scope": "/",
        "icons": [
            {
                "src": "/icons/icon-192.png",
                "sizes": "192x192",
                "type": "image/png",
                "purpose": "any maskable",
            },
            {
                "src": "/icons/icon-512.png",
                "sizes": "512x512",
                "type": "image/png",
                "purpose": "any maskable",
            },
            {
                "src": "/icons/apple-touch-icon.png",
                "sizes": "180x180",
                "type": "image/png",
            },
        ],
    }

    response = make_response(jsonify(data))
    response.headers["Content-Type"] = "application/manifest+json"
    response.headers["Cache-Control"] = "no-cache"
    return response


@pwa_bp.route("/service-worker.js")
def service_worker():
    """
    Serve the service worker from public_html/ with the mandatory
    Service-Worker-Allowed header so the SW can control scope '/'.
    Flask's static serving (via /static/*) can't set this header, so we
    serve it through a dedicated route instead.
    """
    response = make_response(send_from_directory(str(_PUBLIC_HTML), "service-worker.js"))
    response.headers["Service-Worker-Allowed"] = "/"
    response.headers["Content-Type"] = "application/javascript"
    response.headers["Cache-Control"] = "no-cache"
    return response


@pwa_bp.route("/icons/<path:filename>")
def icons(filename: str):
    """Serve icon files from public_html/icons/."""
    icons_dir = _PUBLIC_HTML / "icons"
    return send_from_directory(str(icons_dir), filename)
