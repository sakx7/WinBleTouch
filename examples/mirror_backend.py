"""Minimal Windows window-capture backend for the mirror examples.

Vendored + trimmed from a separate iPhone-mirroring project — just the pieces
`_mirror.py` needs: find the mirror window by title, work out the phone-screen
rectangle inside it, and grab that window's pixels (even when it's occluded).

Windows-only. Needs Pillow. Not part of the WinBleTouch library.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass

from PIL import Image

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

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
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetWindowDC.argtypes = [wintypes.HWND]
user32.GetWindowDC.restype = wintypes.HDC
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
user32.PrintWindow.restype = wintypes.BOOL
gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HANDLE]
gdi32.SelectObject.restype = wintypes.HANDLE
gdi32.DeleteObject.argtypes = [wintypes.HANDLE]
gdi32.DeleteDC.argtypes = [wintypes.HDC]

PW_RENDERFULLCONTENT = 2
DIB_RGB_COLORS = 0
BI_RGB = 0


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


gdi32.GetDIBits.argtypes = [
    wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
    wintypes.LPVOID, ctypes.POINTER(BITMAPINFO), wintypes.UINT,
]


def enable_dpi_awareness() -> None:
    try:
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except (AttributeError, OSError):
        user32.SetProcessDPIAware()


def capture_window(hwnd: int) -> tuple[Image.Image, wintypes.RECT]:
    """PrintWindow grab of hwnd. Raises OSError if the window rejects it
    (some GPU-backed windows do) — caller should fall back to a screen grab."""
    rect = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        raise OSError("GetWindowRect failed")
    width, height = rect.right - rect.left, rect.bottom - rect.top
    if width < 2 or height < 2:
        raise OSError("window is minimized")
    window_dc = user32.GetWindowDC(hwnd)
    memory_dc = gdi32.CreateCompatibleDC(window_dc)
    bitmap = gdi32.CreateCompatibleBitmap(window_dc, width, height)
    previous = gdi32.SelectObject(memory_dc, bitmap)
    try:
        rendered = user32.PrintWindow(hwnd, memory_dc, PW_RENDERFULLCONTENT)
        info = BITMAPINFO()
        info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        info.bmiHeader.biWidth = width
        info.bmiHeader.biHeight = -height
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        info.bmiHeader.biCompression = BI_RGB
        raw = ctypes.create_string_buffer(width * height * 4)
        scanlines = gdi32.GetDIBits(
            memory_dc, bitmap, 0, height, raw, ctypes.byref(info), DIB_RGB_COLORS
        )
        if not rendered or not scanlines:
            raise OSError("PrintWindow failed")
        return Image.frombuffer("RGB", (width, height), raw.raw, "raw", "BGRX", 0, 1).copy(), rect
    finally:
        gdi32.SelectObject(memory_dc, previous)
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(memory_dc)
        user32.ReleaseDC(hwnd, window_dc)


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
    aspect-fitted phone-screen rectangle inside it. `inset` trims fixed
    chrome (left, top, right, bottom, in client px); pass (0,0,0,0) for none."""

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
