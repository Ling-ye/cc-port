"""Generate the CC Port desktop icon set.

The mark is a white bridge-and-arrow glyph on a blue-to-cyan gradient. It is
drawn entirely from geometry so every output is deterministic and contains no
embedded text or font dependency. Output defaults to ``desktop/src-tauri/icons``.

Usage::

    python tools/packaging/icons/generate_icons.py
    python tools/packaging/icons/generate_icons.py --out path/to/icons
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUT = ROOT / "desktop" / "src-tauri" / "icons"

BLUE = (15, 70, 190, 255)
CYAN = (13, 201, 196, 255)
WHITE = (255, 255, 255, 255)
MASTER_SIZE = 1024
SUPERSAMPLE = 4


def _mix(start: int, end: int, amount: float) -> int:
    return round(start + (end - start) * amount)


def _gradient(size: int) -> Image.Image:
    image = Image.new("RGBA", (size, size))
    draw = ImageDraw.Draw(image)
    denominator = max(1, size - 1)
    for y in range(size):
        amount = y / denominator
        color = tuple(_mix(BLUE[channel], CYAN[channel], amount) for channel in range(3))
        draw.line((0, y, size, y), fill=(*color, 255))
    return image


def _bridge_points(size: int) -> list[tuple[int, int]]:
    return [
        (round(size * 0.23), round(size * 0.56)),
        (round(size * 0.29), round(size * 0.45)),
        (round(size * 0.38), round(size * 0.36)),
        (round(size * 0.50), round(size * 0.32)),
        (round(size * 0.62), round(size * 0.36)),
        (round(size * 0.71), round(size * 0.45)),
        (round(size * 0.76), round(size * 0.54)),
    ]


def _draw_icon(size: int) -> Image.Image:
    render_size = size * SUPERSAMPLE
    image = _gradient(render_size)

    corner_mask = Image.new("L", (render_size, render_size), 0)
    mask_draw = ImageDraw.Draw(corner_mask)
    inset = max(1, round(render_size * 0.015))
    mask_draw.rounded_rectangle(
        (inset, inset, render_size - inset - 1, render_size - inset - 1),
        radius=round(render_size * 0.20),
        fill=255,
    )
    image.putalpha(corner_mask)

    draw = ImageDraw.Draw(image)
    stroke = max(4, round(render_size * 0.055))
    deck_y = round(render_size * 0.59)
    deck_start = round(render_size * 0.18)
    deck_end = round(render_size * 0.73)

    draw.line(
        _bridge_points(render_size),
        fill=WHITE,
        width=stroke,
        joint="curve",
    )
    draw.line(
        (deck_start, deck_y, deck_end, deck_y),
        fill=WHITE,
        width=stroke,
    )

    for x, top in (
        (0.31, 0.45),
        (0.40, 0.36),
        (0.50, 0.32),
        (0.60, 0.36),
        (0.69, 0.45),
    ):
        draw.line(
            (
                round(render_size * x),
                round(render_size * top),
                round(render_size * x),
                deck_y,
            ),
            fill=WHITE,
            width=max(2, round(stroke * 0.32)),
        )

    support_width = max(3, round(stroke * 0.50))
    for x in (0.29, 0.66):
        draw.line(
            (
                round(render_size * x),
                deck_y,
                round(render_size * x),
                round(render_size * 0.75),
            ),
            fill=WHITE,
            width=support_width,
        )

    arrow_tip = (round(render_size * 0.84), deck_y)
    arrow_back_top = (round(render_size * 0.70), round(render_size * 0.47))
    arrow_back_bottom = (round(render_size * 0.70), round(render_size * 0.71))
    draw.polygon((arrow_tip, arrow_back_top, arrow_back_bottom), fill=WHITE)

    return image.resize((size, size), Image.Resampling.LANCZOS)


def write_icons(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    base = _draw_icon(MASTER_SIZE)
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
