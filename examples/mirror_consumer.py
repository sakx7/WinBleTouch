#!/usr/bin/env python3
"""EXAMPLE CONSUMER — not part of the WinBleTouch library.

The downstream half of the pipeline, for a coordinate stream that isn't a mouse
over a mirror window (a captured pointer feed, an automation script, etc.):

    source coordinates
        -> Mapper   (crop -> rotate -> normalize -> 0..10000)   <-- your job
        -> contact(x, y) / release()                            <-- the library

    # one "x y" (source pixels) or "up" per line:
    cat points.txt | python examples/mirror_consumer.py 480 1000
"""
from __future__ import annotations
import socket
import sys

HOST, PORT = "127.0.0.1", 8760


class Mapper:
    """Source pixels -> 0..10000 HID units.

    crop   = (left, top, right, bottom) in source pixels, to strip letterbox
             bars before normalizing.
    rotate = 0 / 90 / 180 / 270, source orientation -> device orientation.
    """

    def __init__(self, width: int, height: int, crop=None, rotate: int = 0):
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


def _selftest() -> None:
    m = Mapper(400, 800)
    assert m.to_hid(200, 400) == (5000, 5000)
    assert m.to_hid(500, -20) == (10000, 0)                       # clamped
    m = Mapper(480, 1000, crop=(40, 100, 440, 900))              # letterbox
    assert m.to_hid(240, 500) == (5000, 5000)
    assert Mapper(100, 100, rotate=90).to_hid(0, 0) == (0, 10000)
    assert Mapper(100, 100, rotate=270).to_hid(0, 0) == (10000, 0)
    print("Mapper selftest ok")


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "selftest":
        return _selftest()
    mapper = Mapper(int(sys.argv[1]), int(sys.argv[2])) if len(sys.argv) > 2 else None
    sock = socket.create_connection((HOST, PORT), timeout=5)

    def send(line: str) -> None:
        sock.sendall((line + "\n").encode("ascii"))

    for raw in sys.stdin:                       # forwarded as-is, no interpolation
        s = raw.strip()
        if not s:
            continue
        if s.lower() in ("up", "release", "r"):
            send("release")
            continue
        a, b = s.replace(",", " ").split()[:2]
        x, y = float(a), float(b)
        if mapper:
            x, y = mapper.to_hid(x, y)
        send(f"contact {int(round(x))} {int(round(y))}")
    send("release")
    sock.close()


if __name__ == "__main__":
    main()
