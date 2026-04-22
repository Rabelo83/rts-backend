#!/usr/bin/env python3
"""
scripts/generate_placeholder_icons.py

Generate placeholder PWA icons for the agency from branding config.
Each icon is a solid circle on a transparent PNG background, filled with
branding.primary_color, with the agency.short_name initials centered in white.

Output files (written to public_html/icons/):
  icon-192.png          192×192  (any maskable)
  icon-512.png          512×512  (any maskable)
  apple-touch-icon.png  180×180
  favicon.ico           32×32 composite

Usage:
  python scripts/generate_placeholder_icons.py
  # or from repo root:
  python -m scripts.generate_placeholder_icons

Re-run this whenever agency_config.yaml branding keys change.
Requires Pillow (pip install Pillow).
"""
import sys
from pathlib import Path

# Resolve project root
_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "utils"))

from agency_config import get_agency_short_name, get_primary_color, get_background_color

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    print("ERROR: Pillow is not installed. Run: pip install Pillow")
    sys.exit(1)


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return tuple(int(h[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def _generate_icon(size: int, output_path: Path) -> None:
    short_name = get_agency_short_name()
    # Use first two characters as initials (e.g. "RTS" → "RT", "AC" → "AC")
    initials = (short_name[:2] if len(short_name) >= 2 else short_name).upper()
    primary = _hex_to_rgb(get_primary_color())
    bg = _hex_to_rgb(get_background_color())

    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Outer background circle
    margin = int(size * 0.04)
    draw.ellipse([margin, margin, size - margin, size - margin], fill=bg + (255,))

    # Inner colored disc (safe zone: 80% of total for maskable)
    inner_margin = int(size * 0.10)
    draw.ellipse(
        [inner_margin, inner_margin, size - inner_margin, size - inner_margin],
        fill=primary + (255,),
    )

    # Text — try to load a system font, fall back to default
    font_size = int(size * 0.32)
    font = None
    for font_path in [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/Library/Fonts/Arial Bold.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
    ]:
        if Path(font_path).exists():
            try:
                font = ImageFont.truetype(font_path, font_size)
                break
            except Exception:
                pass
    if font is None:
        font = ImageFont.load_default()

    # Center the text
    bbox = draw.textbbox((0, 0), initials, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    x = (size - text_w) / 2 - bbox[0]
    y = (size - text_h) / 2 - bbox[1]
    draw.text((x, y), initials, fill=(255, 255, 255, 255), font=font)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(output_path), "PNG")
    print(f"  wrote {output_path.relative_to(_ROOT)} ({size}×{size})")


def _generate_favicon(output_path: Path) -> None:
    """Generate a favicon.ico (multi-size: 16, 32, 48)."""
    images = []
    for sz in (16, 32, 48):
        short_name = get_agency_short_name()
        initials = (short_name[:2] if len(short_name) >= 2 else short_name).upper()
        primary = _hex_to_rgb(get_primary_color())
        img = Image.new("RGBA", (sz, sz), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        m = max(1, int(sz * 0.04))
        draw.ellipse([m, m, sz - m, sz - m], fill=primary + (255,))
        # Only render text at 32+ (too small at 16)
        if sz >= 32:
            font_size = max(8, int(sz * 0.40))
            font = None
            for fp in [
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                "/Library/Fonts/Arial Bold.ttf",
                "/System/Library/Fonts/Helvetica.ttc",
            ]:
                if Path(fp).exists():
                    try:
                        font = ImageFont.truetype(fp, font_size)
                        break
                    except Exception:
                        pass
            if font is None:
                font = ImageFont.load_default()
            letter = initials[0]
            bb = draw.textbbox((0, 0), letter, font=font)
            tw, th = bb[2] - bb[0], bb[3] - bb[1]
            draw.text(
                ((sz - tw) / 2 - bb[0], (sz - th) / 2 - bb[1]),
                letter,
                fill=(255, 255, 255, 255),
                font=font,
            )
        images.append(img)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    images[0].save(
        str(output_path),
        format="ICO",
        sizes=[(16, 16), (32, 32), (48, 48)],
        append_images=images[1:],
    )
    print(f"  wrote {output_path.relative_to(_ROOT)} (16/32/48)")


def main() -> None:
    icons_dir = _ROOT / "public_html" / "icons"
    print(f"Generating icons → {icons_dir}")
    print(f"  agency : {get_agency_short_name()}")
    print(f"  color  : {get_primary_color()}")
    print()

    _generate_icon(192, icons_dir / "icon-192.png")
    _generate_icon(512, icons_dir / "icon-512.png")
    _generate_icon(180, icons_dir / "apple-touch-icon.png")
    _generate_favicon(icons_dir / "favicon.ico")

    print("\nDone. Commit the icons directory.")


if __name__ == "__main__":
    main()
