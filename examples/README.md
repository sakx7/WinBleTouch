# Examples

None of this is part of the library. It shows the **consumer** side — turning
some app's coordinates into the `0..10000` HID space and calling
`contact()` / `release()`.

| File | Needs | What it does |
|---|---|---|
| `mirror_consumer.py` | `winbletouch` only | `Mapper` (source px → `0..10000`, crop + rotate) + `forward_stream` (one report per event, no interpolation) + `box` / `spiral` / `forward` demos. Pure, no capture backend. |
| `test_mapper.py` | — | Layer test for `Mapper`. No BLE. `python examples/test_mapper.py` |
| `_mirror.py` | mirror backend | Captures the **full** iPhone display (no status-bar / home-indicator inset) so a plain normalize is exact. |
| `draw_on_mirror.py` | `opencv-python`, `numpy`, `_mirror.py` | Live window of the iPhone mirror; left-drag draws into whatever app is open on the phone. |
| `calibrate_mirror.py` | same as above | 2-point affine calibration, for stream sources where you *can't* get the full-screen rect. Not needed for the mirror. |

## Running

All of these except `test_mapper.py` need the **WinBleTouch service already
running** (`dotnet run -c Release` in the repo root) with the iPhone paired and
subscribed.

```bash
pip install -r examples/requirements.txt

# no capture backend, no BLE — just checks the mapper math
python examples/test_mapper.py

# scripted strokes (needs the service; no mirror backend)
python examples/mirror_consumer.py box
python examples/mirror_consumer.py spiral
python examples/mirror_consumer.py forward 480 1000 < points.txt   # "x y" or "up" per line

# live drawing on the phone (needs the service + mirror backend, see below)
set IPHONE_MIRROR_DIR=C:\path\to\mirror\project   # PowerShell: $env:IPHONE_MIRROR_DIR=...
python examples/draw_on_mirror.py
python examples/calibrate_mirror.py               # only for non-full-screen sources
```

## The mirror backend

`_mirror.py`, `draw_on_mirror.py` and `calibrate_mirror.py` depend on an iPhone
screen-mirroring project (`recorder/`, `runtime/`) that is **not bundled here**.
Set `IPHONE_MIRROR_DIR` to the folder containing it.

For any other setup (QuickTime capture, a mirroring app, a game window, a
drawing canvas), write your own function that returns the current frame as a
`PIL.Image` of the full display and adapt `to_hid`. The library never changes.

```bash
pip install -r examples/requirements.txt
```
