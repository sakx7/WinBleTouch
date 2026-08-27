"""EXAMPLE helper -- capture the WHOLE iPhone screen from a mirror window.

The iOS HID digitizer maps 0..10000 to the *full physical display*, so the
capture must be the full display too (status bar + home indicator included) for a
plain `px / size * 10000` normalize to be correct.

`mirror_backend.py` (vendored, Windows-only) finds a window whose title contains
"iPhone" and returns the aspect-fitted phone-screen rectangle inside it. Point
your mirroring app at that: iPhoneMirror, an AirPlay receiver window, QuickTime,
etc. -- anything that shows the phone edge-to-edge and has "iPhone" in the title.

For any other source, write your own capture returning a PIL.Image of the full
display and skip this module.
"""
from __future__ import annotations

from PIL import Image, ImageGrab

from mirror_backend import MirrorLocator, capture_window, enable_dpi_awareness


class FullScreenMirror:
    """capture() -> PIL.Image of the entire iPhone display."""

    def __init__(self, title: str = "iPhone", aspect=(1170, 2532)):
        enable_dpi_awareness()
        self.locator = MirrorLocator(title, aspect[0], aspect[1], inset=(0, 0, 0, 0))

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
            raise RuntimeError(
                "no visible window with 'iPhone' in the title — open your mirror app"
            )
        try:
            frame, win = capture_window(self.locator.hwnd)
        except OSError:
            from ctypes import byref, wintypes
            from mirror_backend import user32
            win = wintypes.RECT()
            if not user32.GetWindowRect(self.locator.hwnd, byref(win)):
                raise RuntimeError("could not read the mirror window rectangle")
            frame = ImageGrab.grab(bbox=(win.left, win.top, win.right, win.bottom),
                                   all_screens=True).convert("RGB")
        left, top = phone.left - win.left, phone.top - win.top
        box = (left, top, left + phone.width, top + phone.height)
        if box[0] < 0 or box[1] < 0 or box[2] > frame.width or box[3] > frame.height:
            raise RuntimeError("phone region falls outside the mirror capture")
        return frame.crop(box).convert("RGB")
