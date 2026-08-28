# Examples

Not part of the library. The **consumer** side — turning some app's coordinates
into the `0..10000` HID space and calling `contact()` / `release()`. Windows
only, stdlib only, no pip installs.

| File | What it does |
|---|---|
| `overlay_draw.py` | **Interactive.** Draw on the iPhone by dragging on your mirroring app's own window. A low-level mouse hook, armed with CapsLock, swallows the left button over the phone rectangle and streams `contact`/`release`. Renders no video — you watch the mirror app's native window. |
| `mirror_consumer.py` | **Non-mouse input.** `Mapper` (crop → rotate → normalize → `0..10000`) plus a stdin forwarder: pipe `x y` / `up` lines in. `selftest` checks the `Mapper` math. |
| `_link.py` | Fire-and-forget client used by `overlay_draw.py`. |
| `mirror_backend.py` | Finds the mirror window and the phone-screen rectangle inside it. |

## overlay_draw.py

Needs: the WinBleTouch service running (`dotnet run -c Release`), the iPhone
paired + subscribed, and a mirror window on screen (any app whose title contains
"iPhoneMirror" / "iPhone" — it auto-picks the one closest to portrait phone
aspect).

```bash
python examples/overlay_draw.py
```

1. It prints `locked on: '<window title>'` and the phone rect.
2. **Toggle CapsLock** → `ARMED — drawing enabled`.
3. **Left-drag over the phone image** → draws. The click is swallowed, so the
   mirror window doesn't see it (a borderless preview won't drag itself away).
4. CapsLock again to disarm; `Ctrl+C` to quit.

`--title "iPad Mirroring"` forces the wide main app window instead of the
detached preview. `--verbose` prints live hook state.

If your mirror preview is a borderless window that still slips around, right-click
it and enable its "Fix Window" / lock-position option.

## mirror_consumer.py

```bash
python examples/mirror_consumer.py selftest
cat points.txt | python examples/mirror_consumer.py 480 1000   # source is 480x1000 px
```

## A different source

`overlay_draw.py` assumes the mirror window shows the phone edge-to-edge. For
anything else — a letterboxed video, a cropped share, a game window — take the
cursor position (or your own frame's pixel), apply your crop/rotation/normalize
(see `Mapper`), and call `contact(x, y)` / `release()`. The library never changes.
