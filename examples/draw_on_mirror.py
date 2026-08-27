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

Capture runs on its own thread: `PrintWindow` grabs are slow and irregular
(~15-30 fps, jittery), so the display loop just shows the most recent frame at a
steady rate instead of blocking on each grab. The mouse -> contact() path never
depended on the frame rate. For a genuinely smooth preview, watch the native
mirror window and use this one only as the click surface, or swap the capture
backend (windows-capture / dxcam / mss).

Run (with the WinBleTouch service already running + iPhone paired):
    python examples/draw_on_mirror.py
Requires: opencv-python, numpy, and a mirror window with "iPhone" in its title
showing the phone edge-to-edge (see examples/_mirror.py).
"""
from __future__ import annotations
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # for winbletouch.py

import cv2  # noqa: E402
import numpy as np  # noqa: E402
from winbletouch import WinTouch  # noqa: E402
from _mirror import FullScreenMirror  # noqa: E402  (uncropped -> plain normalize is correct)

WIN = "draw on iPhone  (left-drag=draw  r=release  q=quit)"
TARGET_H = 1000  # on-screen height of the mirror view (size knob)


class CaptureThread(threading.Thread):
    """Grabs mirror frames as fast as PrintWindow allows and keeps only the
    latest one. Display never waits on a grab."""

    def __init__(self, cap: FullScreenMirror):
        super().__init__(daemon=True)
        self._cap = cap
        self._lock = threading.Lock()
        self._frame: np.ndarray | None = None
        self._stop = threading.Event()
        self.fps = 0.0
        self.error: str | None = None

    def run(self) -> None:
        last = time.perf_counter()
        while not self._stop.is_set():
            try:
                bgr = cv2.cvtColor(np.array(self._cap.capture()), cv2.COLOR_RGB2BGR)
                self.error = None
            except Exception as e:  # window closed / not found -> keep trying
                self.error = str(e)
                time.sleep(0.2)
                continue
            with self._lock:
                self._frame = bgr
            now = time.perf_counter()
            dt = now - last
            last = now
            self.fps = 0.9 * self.fps + 0.1 * (1.0 / dt) if dt > 0 else self.fps

    def latest(self) -> np.ndarray | None:
        with self._lock:
            return None if self._frame is None else self._frame

    def stop(self) -> None:
        self._stop.set()


def main() -> int:
    touch = WinTouch()
    print("connected:", touch.status())

    grabber = CaptureThread(FullScreenMirror("iPhone"))
    grabber.start()

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
    placeholder = np.zeros((TARGET_H, TARGET_H // 2, 3), np.uint8)

    try:
        while True:
            frame = grabber.latest()
            if frame is None:
                disp = placeholder.copy()
                msg = grabber.error or "waiting for mirror window..."
                cv2.putText(disp, msg[:40], (10, 30), cv2.FONT_HERSHEY_SIMPLEX,
                            0.5, (200, 200, 200), 1)
            else:
                scale = TARGET_H / frame.shape[0]
                disp = cv2.resize(frame, None, fx=scale, fy=scale,
                                  interpolation=cv2.INTER_LINEAR)
            st["h"], st["w"] = disp.shape[:2]

            if 0 <= st["px"] < st["w"] and 0 <= st["py"] < st["h"]:
                colour = (0, 0, 255) if st["down"] else (0, 200, 255)
                cv2.circle(disp, (st["px"], st["py"]), 7, colour, 2)
            cv2.putText(disp, f'{"DRAW" if st["down"] else "idle"}  {grabber.fps:4.1f} cap fps',
                        (10, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
            cv2.imshow(WIN, disp)

            k = cv2.waitKey(8) & 0xFF   # ~120 Hz display poll, independent of capture
            if k in (ord("q"), 27):
                break
            if k == ord("r"):
                st["down"] = False
                touch.release()
    finally:
        grabber.stop()
        touch.release()
        touch.close()
        cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
