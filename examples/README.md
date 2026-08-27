# Examples

None of this is part of the library. It shows the **consumer** side — turning
some app's coordinates into the `0..10000` HID space and calling
`contact()` / `release()`.

| File | Needs | What it does |
|---|---|---|
| `mirror_consumer.py` | `winbletouch` only | `Mapper` (source px → `0..10000`, crop + rotate) + `forward_stream` (one report per event, no interpolation) + `box` / `spiral` / `forward` demos. Pure, no capture. |
| `test_mapper.py` | — | Layer test for `Mapper`. No BLE. `python examples/test_mapper.py` |
| `mirror_backend.py` | `pillow` | Windows window-capture helper (find window by title, PrintWindow grab). Vendored + trimmed from a separate iPhone-mirroring project. |
| `_mirror.py` | `mirror_backend` | `FullScreenMirror.capture()` → PIL image of the **full** iPhone display (no status-bar / home-indicator inset) so a plain normalize is exact. |
| `draw_on_mirror.py` | `opencv-python`, `numpy`, `_mirror` | Live window of the iPhone mirror; left-drag draws into whatever app is open on the phone. |
| `calibrate_mirror.py` | same as above | 2-point affine calibration, for stream sources where you *can't* get the full-screen rect. Not needed for a full-screen mirror. |

## Running

Everything except `test_mapper.py` needs the **WinBleTouch service already
running** (`dotnet run -c Release` in the repo root) with the iPhone paired and
subscribed. `draw_on_mirror.py` / `calibrate_mirror.py` also need a mirror window
with **"iPhone" in its title**, showing the phone edge-to-edge — e.g. iPhoneMirror,
an AirPlay-receiver window, or QuickTime.

```bash
pip install -r examples/requirements.txt

# no capture, no BLE — just checks the mapper math
python examples/test_mapper.py

# scripted strokes (needs the service; no capture)
python examples/mirror_consumer.py box
python examples/mirror_consumer.py spiral
python examples/mirror_consumer.py forward 480 1000 < points.txt   # "x y" or "up" per line

# live drawing on the phone (needs the service + a mirror window)
python examples/draw_on_mirror.py
python examples/calibrate_mirror.py               # only for non-full-screen sources
```

## Using a different mirror / source

`_mirror.py` assumes the mirror shows the phone edge-to-edge and has "iPhone" in
the window title. For anything else — a letterboxed video, a cropped share, a
game window, a drawing canvas — write your own function that returns the current
frame as a `PIL.Image` of the full display and adapt `to_hid`. The library never
changes.
