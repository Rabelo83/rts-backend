"""
Shared validation utilities for RTS Backend
Ensures consistent validation across all services
"""
import re
from typing import Optional


def normalize_stop_id(stop_id: str) -> Optional[str]:
    """
    Normalize a stop ID to 4 digits with consistent behavior across frontend and backend.

    Rules:
    - Extracts only digits from input
    - Accepts 1-4 digit stop IDs
    - Pads with leading zeros to 4 digits
    - Rejects inputs with more than 4 digits (returns None)
    - Rejects empty or non-numeric inputs (returns None)

    Examples:
        "1" -> "0001"
        "773" -> "0773"
        "0773" -> "0773"
        "1192" -> "1192"
        "12345" -> None (too many digits)
        "abc" -> None (no digits)
        "" -> None (empty)

    Args:
        stop_id: Raw stop ID string from user input

    Returns:
        Normalized 4-digit stop ID string or None if invalid
    """
    if not stop_id:
        return None

    # Extract only digits
    digits = re.sub(r"[^0-9]", "", str(stop_id))

    # Reject empty or too long
    if not digits or len(digits) > 4:
        return None

    # Pad to 4 digits
    return digits.zfill(4)


def validate_stop_id(stop_id: str) -> dict:
    """
    Validate a stop ID and return detailed validation result.

    Args:
        stop_id: Raw stop ID string

    Returns:
        Dictionary with validation details:
        {
            "valid": bool,
            "normalized": str or None,
            "error_code": str or None,
            "error_message": str or None
        }
    """
    if not stop_id:
        return {
            "valid": False,
            "normalized": None,
            "error_code": "EMPTY_INPUT",
            "error_message": "Stop ID cannot be empty"
        }

    digits = re.sub(r"[^0-9]", "", str(stop_id))

    if not digits:
        return {
            "valid": False,
            "normalized": None,
            "error_code": "NO_DIGITS",
            "error_message": "Stop ID must contain numeric digits"
        }

    if len(digits) > 4:
        return {
            "valid": False,
            "normalized": None,
            "error_code": "TOO_MANY_DIGITS",
            "error_message": f"Stop ID cannot exceed 4 digits (found {len(digits)})"
        }

    normalized = digits.zfill(4)
    return {
        "valid": True,
        "normalized": normalized,
        "error_code": None,
        "error_message": None
    }


def normalize_route_id(route_id: str) -> Optional[str]:
    """
    Normalize route ID by extracting digits only.

    Examples:
        "1" -> "1"
        "Route 5" -> "5"
        "34" -> "34"
        "abc" -> None

    Args:
        route_id: Raw route ID string

    Returns:
        Digits-only route ID or None if no digits found
    """
    if not route_id:
        return None

    digits = re.sub(r"[^0-9]", "", str(route_id))
    return digits if digits else None
