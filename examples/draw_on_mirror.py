#!/usr/bin/env python3
"""EXAMPLE CONSUMER -- live finger-painting on the iPhone through the mirror.

    left-drag in the window  -> draws on the iPhone
    r                        -> force release
    q / Esc                  -> quit

This is NOT part of the library. It demonstrates the consumer's half:

    full-screen mirror frame (PIL)
        -> window pixel under the cursor
        -> normalize by the displayed frame size
        -> 0..10000 HID coordinates          <-- THIS app's mapping, not the lib's
        -> WinTouch.contact(x, y) / .release()
        -> WinBleTouch -> iPhone

`_mirror.FullScreenMirror` gives the FULL iPhone display (status bar + home
indicator included), so the mapping here is a plain normalize -- no calibration.
A real overlay over a letterboxed video would add a crop; a rotated stream would
add a rotation. The library never sees any of that -- only contact()/release().

Run:
    python examples/draw_on_mirror.py
Requires: opencv-python, numpy, the iPhone mirror window open, and
IPHONE_MIRROR_DIR set (see examples/_mirror.py). The mirror backend is a
separate private project and is not bundled with WinBleTouch.
"""
from __future__ import annotations
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # for winbletouch.py

import cv2  # noqa: E402
import numpy as np  # noqa: E402
from winbletouch import WinTouch  # noqa: E402
from _mirror import FullScreenMirror  # noqa: E402  (uncropped -> plain normalize is correct)

WIN = "draw on iPhone  (left-drag=draw  r=release  q=quit)"
TARGET_H = 900  # on-screen height of the mirror view


def main() -> int:
    cap = FullScreenMirror("iPhone")
    touch = WinTouch()
    print("connected:", touch.status())

    st = {"down": False, "w": 1, "h": 1, "px": -1, "py": -1}

    def to_hid(x: int, y: int) -> tuple[float, float]:
        nx = min(max(x / st["w"], 0.0), 1.0)
        ny = min(max(y / st["h"], 0.0), 1.0)
        return nx * 10000.0, ny * 10000.0

    def on_mouse(event, x, y, flags, _param):
        st["px"], st["py"] = x, y
        if event == cv2.EVENT_LBUTTONDOWN:
            st["down"] = True
            touch.contact(*to_hid(x, y))
        elif event == cv2.EVENT_MOUSEMOVE and st["down"]:
            touch.contact(*to_hid(x, y))          # forwarded as-is, no interpolation
        elif event == cv2.EVENT_LBUTTONUP:
            st["down"] = False
            touch.release()

    cv2.namedWindow(WIN, cv2.WINDOW_AUTOSIZE)
    cv2.setMouseCallback(WIN, on_mouse)

    try:
        while True:
            frame = cv2.cvtColor(np.array(cap.capture()), cv2.COLOR_RGB2BGR)
            scale = TARGET_H / frame.shape[0]
            disp = cv2.resize(frame, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
            st["h"], st["w"] = disp.shape[:2]

            if 0 <= st["px"] < st["w"] and 0 <= st["py"] < st["h"]:
                colour = (0, 0, 255) if st["down"] else (0, 200, 255)
                cv2.circle(disp, (st["px"], st["py"]), 7, colour, 2)
            cv2.putText(disp, "DRAW" if st["down"] else "idle", (10, 26),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
            cv2.imshow(WIN, disp)

            k = cv2.waitKey(16) & 0xFF
            if k in (ord("q"), 27):
                break
            if k == ord("r"):
                st["down"] = False
                touch.release()
    finally:
        touch.release()
        touch.close()
        cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
