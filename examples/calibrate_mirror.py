#!/usr/bin/env python3
"""EXAMPLE CONSUMER helper -- calibrate window pixels -> 0..10000 HID space.

For the iPhone mirror you do NOT need this -- use _mirror.FullScreenMirror, which
captures the whole display so a plain normalize is exact. Keep this for any
source where you can't get the true full-screen rectangle (letterboxed video
feed, cropped screen-share, unknown preview geometry): it finds the real per-axis
affine transform from two reference points and writes examples/mirror_calib.json.

Procedure (run with a BLANK drawing canvas open on the phone):
    1. The script draws a "+" at HID (2000, 2000). Look at the mirror window and
       LEFT-CLICK exactly where that mark appears.
    2. It draws a "+" at HID (8000, 8000). LEFT-CLICK where that one appears.
    3. It solves hx = ax*wx + bx, hy = ay*wy + by and saves them.
Press q to abort.
"""
from __future__ import annotations
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))  # for winbletouch.py

import cv2  # noqa: E402
import numpy as np  # noqa: E402
from winbletouch import WinTouch  # noqa: E402
from _mirror import FullScreenMirror  # noqa: E402

CALIB_PATH = HERE / "mirror_calib.json"
WIN = "calibrate  (click the drawn '+' marks;  q = abort)"
TARGET_H = 900
REFS = [(2000, 2000), (8000, 8000)]  # HID points to draw, in order


def draw_plus(t: WinTouch, hx: float, hy: float, arm: float = 400.0) -> None:
    t.contact(hx - arm, hy); t.contact(hx + arm, hy); t.release()
    time.sleep(0.05)
    t.contact(hx, hy - arm); t.contact(hx, hy + arm); t.release()


def main() -> int:
    cap = FullScreenMirror("iPhone")
    t = WinTouch()
    print("connected:", t.status())

    clicks: list[tuple[int, int]] = []
    idx = {"i": 0, "w": 1, "h": 1}

    def on_mouse(event, x, y, flags, _):
        if event == cv2.EVENT_LBUTTONDOWN and idx["i"] < len(REFS):
            clicks.append((x, y))
            print(f"  ref {idx['i']+1}: window ({x},{y}) <-> HID {REFS[idx['i']]}")
            idx["i"] += 1

    cv2.namedWindow(WIN, cv2.WINDOW_AUTOSIZE)
    cv2.setMouseCallback(WIN, on_mouse)

    drawn = -1
    try:
        while idx["i"] < len(REFS):
            if drawn < idx["i"]:
                hx, hy = REFS[idx["i"]]
                print(f"drawing '+' at HID ({hx},{hy}) -- click it in the window")
                draw_plus(t, hx, hy)
                drawn = idx["i"]

            frame = cv2.cvtColor(np.array(cap.capture()), cv2.COLOR_RGB2BGR)
            scale = TARGET_H / frame.shape[0]
            disp = cv2.resize(frame, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
            idx["h"], idx["w"] = disp.shape[:2]
            cv2.putText(disp, f"click ref {idx['i']+1}/{len(REFS)}", (10, 26),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
            cv2.imshow(WIN, disp)
            if (cv2.waitKey(16) & 0xFF) == ord("q"):
                print("aborted"); return 1
    finally:
        t.release()

    (wx0, wy0), (wx1, wy1) = clicks
    (hx0, hy0), (hx1, hy1) = REFS
    ax = (hx1 - hx0) / (wx1 - wx0); bx = hx0 - ax * wx0
    ay = (hy1 - hy0) / (wy1 - wy0); by = hy0 - ay * wy0
    calib = {"ax": ax, "bx": bx, "ay": ay, "by": by,
             "disp_w": idx["w"], "disp_h": idx["h"]}
    CALIB_PATH.write_text(json.dumps(calib, indent=2))
    print("\nsaved", CALIB_PATH)
    print(f"  hx = {ax:.3f} * wx + {bx:.1f}")
    print(f"  hy = {ay:.3f} * wy + {by:.1f}")
    t.close()
    cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
