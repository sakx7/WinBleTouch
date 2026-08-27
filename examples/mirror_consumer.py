#!/usr/bin/env python3
"""EXAMPLE CONSUMER — not part of the WinBleTouch library.

Shows the downstream half of the pipeline:

    preview/source coordinates
          -> Mapper  (de-letterbox -> rotation -> normalize -> 0..10000)
          -> WinTouch.contact / .release   (the library)
          -> Windows BLE HID digitizer -> iPhone/iPad

`Mapper` and the stream forwarding here are app concerns. Copy/adapt them; the
library itself only ever sees absolute 0..10000 coordinates.

Usage:
    python examples/mirror_consumer.py box
    python examples/mirror_consumer.py spiral
    cat points.txt | python examples/mirror_consumer.py forward 480 1000
        # each line: "x y" (source pixels) or "up"
"""
from __future__ import annotations
import math
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from winbletouch import WinTouch  # noqa: E402


class Mapper:
    """Source pixels -> 0..10000 HID units.

    crop   = (left, top, right, bottom) in source pixels, to strip letterbox
             bars before normalizing.
    rotate = 0 / 90 / 180 / 270, source orientation -> device orientation.
    """

    def __init__(self, width: int, height: int, crop=None, rotate: int = 0):
        self.w, self.h = width, height
        self.crop = crop or (0, 0, width, height)
        self.rotate = rotate % 360

    def to_hid(self, px: float, py: float) -> tuple[float, float]:
        l, t, r, b = self.crop
        nx = (px - l) / (r - l)
        ny = (py - t) / (b - t)
        if self.rotate == 90:
            nx, ny = ny, 1 - nx
        elif self.rotate == 180:
            nx, ny = 1 - nx, 1 - ny
        elif self.rotate == 270:
            nx, ny = 1 - ny, nx
        nx = min(max(nx, 0.0), 1.0)
        ny = min(max(ny, 0.0), 1.0)
        return nx * 10000, ny * 10000


def forward_stream(t: WinTouch, lines, mapper: Mapper | None = None) -> None:
    """Forward a live pointer stream as-is: one contact() per position event,
    release() on 'up'. No interpolation, no synthesised timing."""
    for raw in lines:
        s = raw.strip()
        if not s:
            continue
        if s.lower() in ("up", "release", "r"):
            t.release()
            continue
        a, b = s.replace(",", " ").split()[:2]
        x, y = float(a), float(b)
        if mapper is not None:
            x, y = mapper.to_hid(x, y)
        t.contact(x, y)


def _scripted_path(t: WinTouch, points, step_delay: float = 0.012) -> None:
    for (x, y) in points:
        t.contact(x, y)
        time.sleep(step_delay)
    t.release()


def main() -> None:
    name = sys.argv[1] if len(sys.argv) > 1 else "help"
    if name not in ("box", "spiral", "forward"):
        print(__doc__)
        return
    t = WinTouch()  # needs the WinBleTouch service running
    print("status:", t.status())
    if name == "box":
        corners = [(2000, 2000), (8000, 2000), (8000, 8000), (2000, 8000), (2000, 2000)]
        pts = []
        for (x0, y0), (x1, y1) in zip(corners, corners[1:]):
            pts += [(x0 + (x1 - x0) * i / 20, y0 + (y1 - y0) * i / 20) for i in range(21)]
        _scripted_path(t, pts)
        print("drew box")
    elif name == "spiral":
        pts = [
            (5000 + (300 + i * 16) * math.cos(i / 240 * math.pi * 8),
             5000 + (300 + i * 16) * math.sin(i / 240 * math.pi * 8))
            for i in range(240)
        ]
        _scripted_path(t, pts, step_delay=0.008)
        print("drew spiral")
    elif name == "forward":
        m = Mapper(int(sys.argv[2]), int(sys.argv[3])) if len(sys.argv) > 3 else None
        forward_stream(t, sys.stdin, m)
        t.release()
        print("stream ended")
    t.close()


if __name__ == "__main__":
    main()
