"""Locate the mirror window and the phone-screen rectangle inside it.

Just the window-geometry pieces `overlay_draw.py` needs, adapted from a separate
iPhone-mirroring project. Windows-only, stdlib only. Not part of the WinBleTouch
library.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass

user32 = ctypes.WinDLL("user32", use_last_error=True)
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = wintypes.BOOL
user32.IsWindow.argtypes = [wintypes.HWND]
user32.IsWindow.restype = wintypes.BOOL
user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]


def enable_dpi_awareness() -> None:
    try:
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except (AttributeError, OSError):
        user32.SetProcessDPIAware()


@dataclass(frozen=True)
class ScreenRect:
    left: int
    top: int
    width: int
    height: int

    @property
    def right(self) -> int:
        return self.left + self.width

    @property
    def bottom(self) -> int:
        return self.top + self.height


class MirrorLocator:
    """Finds a window whose title contains `title_fragment` and returns the
    aspect-fitted phone-screen rectangle inside it (screen coords). `inset`
    trims fixed chrome (left, top, right, bottom, in client px)."""

    def __init__(self, title_fragment: str, aspect_width: float,
                 aspect_height: float, inset: tuple[int, int, int, int] = (0, 0, 0, 0)):
        self.title_fragment = title_fragment.casefold()
        self.target_aspect = aspect_width / aspect_height
        self.inset = inset
        self.hwnd: int | None = None
        self.title = ""

    def _find_window(self) -> int | None:
        matches: list[tuple[int, str]] = []

        @WNDENUMPROC
        def callback(hwnd: int, _param: int) -> bool:
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buffer, length + 1)
            if self.title_fragment in buffer.value.casefold():
                matches.append((hwnd, buffer.value))
            return True

        user32.EnumWindows(callback, 0)
        if not matches:
            return None
        self.title = matches[0][1]
        return matches[0][0]

    def locate(self) -> ScreenRect | None:
        if not self.hwnd or not user32.IsWindow(self.hwnd):
            self.hwnd = self._find_window()
        if not self.hwnd:
            return None

        client = wintypes.RECT()
        origin = wintypes.POINT(0, 0)
        if not user32.GetClientRect(self.hwnd, ctypes.byref(client)):
            self.hwnd = None
            return None
        if not user32.ClientToScreen(self.hwnd, ctypes.byref(origin)):
            self.hwnd = None
            return None

        cw, ch = client.right - client.left, client.bottom - client.top
        if cw < 2 or ch < 2:
            return None

        # Fit the phone aspect ratio inside the client area (strip letterbox).
        if cw / ch > self.target_aspect:
            ph = ch
            pw = round(ph * self.target_aspect)
            pl = origin.x + (cw - pw) // 2
            pt = origin.y
        else:
            pw = cw
            ph = round(pw / self.target_aspect)
            pl = origin.x
            pt = origin.y + (ch - ph) // 2

        il, it, ir, ib = self.inset
        pl, pt = pl + il, pt + it
        pw, ph = pw - il - ir, ph - it - ib
        if pw < 2 or ph < 2:
            return None
        return ScreenRect(pl, pt, pw, ph)
