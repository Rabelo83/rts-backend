"""
utils/agency_config.py
Single source of truth for agency-specific configuration.

Loads agency_config.yaml once (module-level cache).
Resolves env-var references (api_key_env -> os.getenv(value)).
Call get_agency_config() from any module that needs agency data.
"""
import os
from functools import lru_cache
from pathlib import Path

import yaml  # PyYAML — already in the Python stdlib fallbacks; add to requirements.txt if missing

_CONFIG_PATH = Path(__file__).resolve().parents[1] / "agency_config.yaml"

_REQUIRED_KEYS = [
    ("agency", "timezone"),
    ("contact", "support_phone"),
    ("contact", "website"),
    ("realtime", "endpoint"),
    ("realtime", "api_key_env"),
]


@lru_cache(maxsize=1)
def get_agency_config() -> dict:
    """
    Load and return the agency config dict.
    Cached after first call — safe to call repeatedly with no I/O overhead.
    Raises RuntimeError if required keys are missing.
    """
    if not _CONFIG_PATH.exists():
        raise RuntimeError(
            f"agency_config.yaml not found at {_CONFIG_PATH}. "
            "Create it before starting the server."
        )

    with _CONFIG_PATH.open("r", encoding="utf-8") as f:
        cfg: dict = yaml.safe_load(f)

    # Validate required keys
    for section, key in _REQUIRED_KEYS:
        if not (cfg.get(section) or {}).get(key):
            raise RuntimeError(
                f"agency_config.yaml is missing required key: {section}.{key}"
            )

    # Resolve env-var references in the realtime section
    api_key_env = cfg["realtime"]["api_key_env"]
    cfg["realtime"]["_resolved_api_key"] = os.getenv(api_key_env, "")

    return cfg


# ── Convenience accessors (avoids deep dict access at call sites) ──────────────

def get_timezone() -> str:
    return get_agency_config()["agency"]["timezone"]


def get_support_phone() -> str:
    return get_agency_config()["contact"]["support_phone"]


def get_support_hours(lang: str = "en") -> str:
    cfg = get_agency_config()["contact"]
    if lang.startswith("es"):
        return cfg.get("support_hours_es", cfg["support_hours"])
    return cfg["support_hours"]


def get_website() -> str:
    return get_agency_config()["contact"]["website"]


def get_agency_full_name() -> str:
    return get_agency_config()["agency"]["full_name"]


def get_agency_short_name() -> str:
    return get_agency_config()["agency"]["short_name"]


def get_geocoding_bbox() -> list:
    """Return [W, S, E, N] bounding box for geocoding queries."""
    return get_agency_config()["geocoding"]["bbox"]


def get_city_hint() -> str:
    return get_agency_config()["geocoding"]["city_hint"]


def get_transfer_hubs() -> list[dict]:
    """Return list of {stop_id, id, display} hub dicts."""
    return get_agency_config()["landmarks"]["hubs"]


def get_landmarks() -> dict:
    """
    Returns the landmarks.coordinates mapping from agency_config.yaml,
    or {} if missing. Shape:
        {canonical_name: {lat, lon, aliases: [lowercase strings]}}
    """
    cfg = get_agency_config()
    return ((cfg.get("landmarks") or {}).get("coordinates") or {})


def get_common_destinations() -> dict:
    """
    Returns {'landmarks': {...}, 'pois': {...}} from agency_config.yaml.
    Empty dict if unset.
    """
    cfg = get_agency_config()
    return cfg.get("common_destinations") or {"landmarks": {}, "pois": {}}


def format_contact_note(lang: str = "en") -> str:
    """Return the standard customer-service fallback line."""
    phone = get_support_phone()
    hours = get_support_hours(lang)
    website = get_website()
    if lang.startswith("es"):
        return f"**{phone}** ({hours}) o visita {website}."
    return f"**{phone}** ({hours}) or visit {website}."


def get_primary_color() -> str:
    """Return hex branding primary color, e.g. '#0057B8'."""
    return get_agency_config()["branding"]["primary_color"]


def get_background_color() -> str:
    """Return hex branding background color, e.g. '#070d1a'."""
    return get_agency_config()["branding"].get("background_color", "#000000")


def get_default_lang() -> str:
    """Return the default language code, e.g. 'en'."""
    return get_agency_config()["languages"]["default"]


def get_city() -> str:
    """Return the agency city name, e.g. 'Gainesville'."""
    return get_agency_config()["agency"]["city"]


def get_contact_email() -> str:
    """Return the agency contact email (used for VAPID subject, etc.)."""
    return get_agency_config()["contact"].get("email", "")


def get_vapid_subject() -> str:
    """
    Return the VAPID subject string.
    Priority: VAPID_SUBJECT env var → mailto:<contact.email> from config.
    Falls back to 'mailto:admin@example.com' if neither is set.
    """
    import os
    env_subj = os.getenv("VAPID_SUBJECT", "").strip()
    if env_subj:
        return env_subj
    email = get_contact_email()
    if email:
        return f"mailto:{email}"
    return "mailto:admin@example.com"
