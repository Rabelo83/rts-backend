#!/usr/bin/env python3
"""
scripts/generate_vapid_keys.py
Print a fresh VAPID key pair to stdout.
Never commit the output — add to .env.local instead.

Usage:
    .tools/python311/bin/python scripts/generate_vapid_keys.py
"""
import base64
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "utils"))

try:
    from py_vapid import Vapid
except ImportError:
    try:
        from pywebpush import Vapid
    except ImportError:
        print("ERROR: Install deps first: pip install -r requirements.txt", file=sys.stderr)
        sys.exit(1)

from cryptography.hazmat.primitives import serialization


def b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode("ascii").rstrip("=")


vapid = Vapid()
vapid.generate_keys()

# Public key: 65-byte uncompressed EC point (0x04 || X || Y), per RFC 5480
pub_bytes = vapid.public_key.public_bytes(
    encoding=serialization.Encoding.X962,
    format=serialization.PublicFormat.UncompressedPoint,
)

# Private key: raw 32-byte scalar (P-256 d value, big-endian)
priv_scalar = vapid.private_key.private_numbers().private_value
priv_bytes = priv_scalar.to_bytes(32, "big")

# Resolve VAPID subject from agency_config if available
try:
    from agency_config import get_contact_email
    subject = f"mailto:{get_contact_email()}"
except Exception:
    subject = "mailto:admin@example.com"

print("# Add these to .env.local — DO NOT COMMIT.")
print("# BACK THEM UP: losing them disconnects every existing push subscriber.")
print(f"VAPID_PUBLIC_KEY={b64url(pub_bytes)}")
print(f"VAPID_PRIVATE_KEY={b64url(priv_bytes)}")
print(f"VAPID_SUBJECT={subject}")
