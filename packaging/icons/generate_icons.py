"""Generate placeholder Tauri icons for the LPM Desktop app.

Produces a simple flat icon with the letters ``LPM`` in white on a deep purple
background. Output directory defaults to ``desktop/src-tauri/icons``.

Usage::

    python packaging/icons/generate_icons.py
    python packaging/icons/generate_icons.py --out path/to/icons
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = ROOT / "desktop" / "src-tauri" / "icons"

BG_COLOR = (88, 64, 168, 255)
FG_COLOR = (255, 255, 255, 255)
TEXT = "LPM"


def _load_font(size: int) -> ImageFont.ImageFont:
    candidates = [
        "C:/Windows/Fonts/segoeuib.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/segoeui.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _draw_icon(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    radius = size // 6
    draw.rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=BG_COLOR)

    font_size = max(8, int(size * 0.42))
    font = _load_font(font_size)
    bbox = draw.textbbox((0, 0), TEXT, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    x = (size - text_w) / 2 - bbox[0]
    y = (size - text_h) / 2 - bbox[1]
    draw.text((x, y), TEXT, fill=FG_COLOR, font=font)
    return img


def write_icons(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    base = _draw_icon(1024)
    base.save(out_dir / "icon.png", format="PNG")

    sizes = [16, 24, 32, 48, 64, 128, 256]
    base.save(
        out_dir / "icon.ico",
        format="ICO",
        sizes=[(s, s) for s in sizes],
    )

    _draw_icon(32).save(out_dir / "32x32.png", format="PNG")
    _draw_icon(128).save(out_dir / "128x128.png", format="PNG")
    _draw_icon(256).save(out_dir / "128x128@2x.png", format="PNG")

    for s in (30, 44, 71, 89, 107, 142, 150, 284, 310):
        _draw_icon(s).save(out_dir / f"Square{s}x{s}Logo.png", format="PNG")
    _draw_icon(50).save(out_dir / "StoreLogo.png", format="PNG")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT,
        help=f"Output directory (default: {DEFAULT_OUT})",
    )
    args = parser.parse_args(argv)

    write_icons(args.out)
    print(f"Icons written to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
