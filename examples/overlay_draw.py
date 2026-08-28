#!/usr/bin/env python3
"""EXAMPLE CONSUMER -- draw on the iPhone by dragging on the mirror app's own window.

Renders no video of its own. You watch the mirroring app's native (smooth, D3D)
window; this installs a low-level mouse hook, and while *armed* it **swallows**
left-button events over the phone rectangle (so the mirror window never sees them
-- a borderless preview won't drag itself away) and maps the cursor position to
`0..10000`, streaming `contact` / `release` to the WinBleTouch service.

    CapsLock            arm / disarm
    left-drag (armed)   draw on the iPhone   (that click does NOT reach the app)
    Ctrl+C              quit

The mapping (cursor - phone rect, normalize) is this script's job. The library
only ever sees contact()/release() in 0..10000.

It auto-picks the window matching --title whose client aspect is closest to a
portrait phone (the detached preview, not the wide main app window).

Run (WinBleTouch service running + iPhone paired, mirror window open):
    python examples/overlay_draw.py
    python examples/overlay_draw.py --title "iPad Mirroring"   # force the main window
    python examples/overlay_draw.py --verbose
Windows only, stdlib only.
"""
from __future__ import annotations
import ctypes
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mirror_backend import MirrorLocator, enable_dpi_awareness      # noqa: E402
from _link import Sender                                            # noqa: E402

ASPECT = (1170, 2532)
VK_LBUTTON, VK_CAPITAL = 0x01, 0x14
WH_MOUSE_LL = 14
WM_MOUSEMOVE, WM_LBUTTONDOWN, WM_LBUTTONUP = 0x0200, 0x0201, 0x0202
PM_REMOVE = 0x0001

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
LRESULT = ctypes.c_ssize_t
ULONG_PTR = wintypes.WPARAM
HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("pt", wintypes.POINT), ("mouseData", wintypes.DWORD),
                ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ULONG_PTR)]


user32.SetWindowsHookExW.restype = wintypes.HHOOK
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
user32.CallNextHookEx.restype = LRESULT
user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.GetKeyState.argtypes = [ctypes.c_int]
user32.GetKeyState.restype = ctypes.c_short
user32.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                                wintypes.UINT, wintypes.UINT, wintypes.UINT]
user32.PeekMessageW.restype = wintypes.BOOL
user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
_WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = [_WNDENUMPROC, wintypes.LPARAM]


def key_down(vk: int) -> bool:
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)


def caps_on() -> bool:
    return bool(user32.GetKeyState(VK_CAPITAL) & 1)   # toggle state (reliable)


def valid_rect(r) -> bool:
    return r is not None and r.width > 50 and r.height > 50 and r.left > -20000


def pick_window(fragment: str) -> tuple[int, str] | None:
    """All visible windows whose title contains `fragment`, preferring the one
    whose client area is closest to portrait phone aspect (the detached preview,
    not the wide main app window)."""
    frag = fragment.casefold()
    hits: list[tuple[float, int, str]] = []

    @_WNDENUMPROC
    def cb(h, _l):
        if not user32.IsWindowVisible(h):
            return True
        n = user32.GetWindowTextLengthW(h)
        if n <= 0:
            return True
        b = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(h, b, n + 1)
        if frag not in b.value.casefold():
            return True
        rc = wintypes.RECT()
        user32.GetClientRect(h, ctypes.byref(rc))
        w, ht = rc.right - rc.left, rc.bottom - rc.top
        if w < 50 or ht < 50:
            return True
        hits.append((abs(w / ht - ASPECT[0] / ASPECT[1]), h, b.value))
        return True

    user32.EnumWindows(cb, 0)
    if not hits:
        return None
    hits.sort()
    return hits[0][1], hits[0][2]


class State:
    armed = False
    rect = None
    want_down = False          # left button held inside the phone rect (armed)
    pt: tuple[int, int] | None = None   # latest mapped HID point
    verbose = False


st = State()
link: Sender | None = None
_sender_wake = threading.Event()


def _sender_loop() -> None:
    down = False
    last = None
    while True:
        _sender_wake.wait(timeout=0.1)
        _sender_wake.clear()
        want, pt = st.want_down, st.pt
        if want and pt is not None:
            if pt != last:
                link.contact(*pt)
                last = pt
                down = True
        elif down:
            link.release()
            down = False
            last = None


@HOOKPROC
def _mouse_hook(ncode, wparam, lparam):
    # Swallow only the button DOWN/UP over the phone rect -- that's enough to stop
    # the mirror window's drag (it never sees the mousedown, so no NCHITTEST move).
    # MOUSEMOVE is passed through so the cursor still moves normally; we just read
    # its position to follow the stroke.
    if ncode >= 0 and st.armed and valid_rect(st.rect):
        ms = ctypes.cast(lparam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
        x, y = ms.pt.x, ms.pt.y
        r = st.rect
        inside = r.left <= x < r.right and r.top <= y < r.bottom
        if wparam == WM_LBUTTONDOWN and inside:
            st.want_down = True
            st.pt = _map(x, y, r)
            _sender_wake.set()
            return 1
        if wparam == WM_MOUSEMOVE and st.want_down:
            st.pt = _map(x, y, r)
            _sender_wake.set()
        elif wparam == WM_LBUTTONUP and st.want_down:
            st.want_down = False
            _sender_wake.set()
            return 1
    return user32.CallNextHookEx(None, ncode, wparam, lparam)


def _map(x, y, r) -> tuple[int, int]:
    nx = min(max((x - r.left) / r.width, 0.0), 1.0)
    ny = min(max((y - r.top) / r.height, 0.0), 1.0)
    return round(nx * 10000), round(ny * 10000)


def main(argv: list[str]) -> int:
    global link
    st.verbose = "--verbose" in argv or "-v" in argv
    title = "iPhoneMirror"
    if "--title" in argv:
        title = argv[argv.index("--title") + 1]

    enable_dpi_awareness()
    picked = pick_window(title)
    if picked is None:
        print(f"no visible window matching '{title}'. Open the mirror window "
              f"(not minimized). Override with --title \"...\".")
        return 1
    hwnd, wtitle = picked
    locator = MirrorLocator(title, ASPECT[0], ASPECT[1], inset=(0, 0, 0, 0))
    locator.hwnd = hwnd                       # use the aspect-picked window
    r = locator.locate()
    if not valid_rect(r):
        print(f"window {wtitle!r} has no usable client rect (minimized?).")
        return 1
    st.rect = r
    print(f"locked on: {wtitle!r}   phone rect {r.width}x{r.height} @ ({r.left},{r.top})")

    link = Sender()
    threading.Thread(target=_sender_loop, daemon=True).start()

    hook = user32.SetWindowsHookExW(WH_MOUSE_LL, _mouse_hook, None, 0)
    if not hook:
        print("SetWindowsHookExW failed:", ctypes.get_last_error())
        return 1
    print("ready.  Toggle CapsLock to arm/disarm.  Left-drag over the phone to draw.\n"
          "TIP: right-click the preview -> \"Fix Window\" so it can't be dragged.\n"
          "Ctrl+C to quit.", flush=True)

    msg = wintypes.MSG()
    caps_prev = caps_on()
    st.armed = False
    n_contacts = 0
    next_tick = time.perf_counter()
    try:
        while True:
            while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))

            now = time.perf_counter()
            if now >= next_tick:
                caps = caps_on()
                if caps != caps_prev:
                    st.armed = not st.armed
                    print("ARMED — drawing enabled" if st.armed else "disarmed", flush=True)
                    if not st.armed and st.want_down:
                        st.want_down = False
                        _sender_wake.set()
                caps_prev = caps

                nr = locator.locate()
                if valid_rect(nr):
                    st.rect = nr

                if st.want_down and st.pt is not None:
                    n_contacts += 1
                    if n_contacts == 1 or n_contacts % 30 == 0:
                        print(f"  drawing @ {st.pt}  ({n_contacts} points)", flush=True)
                else:
                    n_contacts = 0

                if st.verbose:
                    print(f"  armed={st.armed} want_down={st.want_down} "
                          f"lbtn={key_down(VK_LBUTTON)} pt={st.pt}", flush=True)
                next_tick = now + (0.2 if st.verbose else 0.1)

            time.sleep(0.003)
    except KeyboardInterrupt:
        pass
    finally:
        user32.UnhookWindowsHookEx(hook)
        if st.want_down:
            link.release()
        link.close()
        print("\nstopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
