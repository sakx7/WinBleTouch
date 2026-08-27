"""EXAMPLE helper -- capture the WHOLE iPhone screen from the mirror, uncropped.

`runtime.visual_state_backend.LiveMirrorCapture` deliberately insets ~5.4% off
the top and ~4.1% off the bottom (iOS status bar + home indicator) because it is
built for visual state-matching of the *usable app area*. But the iOS HID
digitizer maps 0..10000 to the *full physical display*, so that inset shows up
downstream as a linear gain+offset error on Y.

This helper reuses the same window locator with a ZERO inset, so what you get
back is the full phone screen and a plain normalize (px / size * 10000) is
correct -- no calibration needed.
"""
from __future__ import annotations
import os
import sys
from pathlib import Path

# The iPhone-mirroring backend (recorder/, runtime/) is a separate, private
# project -- it is NOT bundled here. Point IPHONE_MIRROR_DIR at the directory
# that contains it. Any other stream source: write your own capture returning a
# PIL.Image of the full display and skip this module entirely.
_dir = os.environ.get("IPHONE_MIRROR_DIR")
if not _dir or not (Path(_dir) / "recorder").is_dir():
    raise SystemExit(
        "examples/_mirror.py needs the iPhone mirror backend.\n"
        "Set IPHONE_MIRROR_DIR to the folder containing recorder/ and runtime/.\n"
        "This backend is not part of WinBleTouch; supply your own capture for\n"
        "any other streaming setup."
    )
sys.path.insert(0, _dir)

from PIL import Image, ImageGrab  # noqa: E402
from recorder import app as backend  # noqa: E402


class FullScreenMirror:
    """capture() -> PIL.Image of the entire iPhone display (no status-bar inset)."""

    def __init__(self, title: str = "iPhone", aspect=(1170, 2532)):
        backend.enable_dpi_awareness()
        self._b = backend
        self.locator = backend.MirrorLocator(title, aspect[0], aspect[1], (0, 0, 0, 0), None)

    def capture(self) -> Image.Image:
        last: Exception | None = None
        for _ in range(3):
            try:
                return self._once()
            except RuntimeError as e:
                last = e
        raise last if last else RuntimeError("mirror capture failed")

    def _once(self) -> Image.Image:
        phone = self.locator.locate()
        if phone is None or self.locator.hwnd is None:
            raise RuntimeError("iPhoneMirror window unavailable")
        try:
            frame, win = self._b.capture_mirror_window(self.locator.hwnd)
            left, top = phone.left - win.left, phone.top - win.top
        except OSError:
            win = self._b.wintypes.RECT()
            if not self._b.user32.GetWindowRect(self.locator.hwnd, self._b.ctypes.byref(win)):
                raise RuntimeError("could not read the iPhoneMirror window rectangle")
            frame = ImageGrab.grab(bbox=(win.left, win.top, win.right, win.bottom),
                                   all_screens=True).convert("RGB")
            left, top = phone.left - win.left, phone.top - win.top
        box = (left, top, left + phone.width, top + phone.height)
        if box[0] < 0 or box[1] < 0 or box[2] > frame.width or box[3] > frame.height:
            raise RuntimeError("phone region falls outside the mirror capture")
        return frame.crop(box).convert("RGB")
