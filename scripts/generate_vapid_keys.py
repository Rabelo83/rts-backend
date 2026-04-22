#!/usr/bin/env python3
"""
scripts/generate_vapid_keys.py
Print a fresh VAPID key pair to stdout.
Never commit the output — add to .env.local instead.

Usage:
    python scripts/generate_vapid_keys.py
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "utils"))

try:
    from py_vapid import Vapid
except ImportError:
    try:
        from pywebpush import Vapid  # pywebpush < 2
    except ImportError:
        print("ERROR: Install pywebpush first:  pip install pywebpush", file=sys.stderr)
        sys.exit(1)

vapid = Vapid()
vapid.generate_keys()

pub = vapid.public_key
priv = vapid.private_key

# pywebpush ≥2 exposes serialize() helpers; fallback for older API
try:
    pub_b64 = vapid.public_key_urlsafe_base64
    priv_b64 = vapid.private_key_urlsafe_base64
except AttributeError:
    import base64, json
    pub_b64 = base64.urlsafe_b64encode(pub).decode().rstrip("=")
    priv_b64 = base64.urlsafe_b64encode(priv).decode().rstrip("=")

print("# Add these to .env.local — DO NOT COMMIT")
print(f"VAPID_PUBLIC_KEY={pub_b64}")
print(f"VAPID_PRIVATE_KEY={priv_b64}")
print("VAPID_SUBJECT=mailto:alfredo.rabelo@am2ar.com")
