# WinBleTouch

**Windows-native BLE HID touchscreen digitizer for iOS, using absolute touch coordinates without AssistiveTouch.**

**WinBleTouch is a .NET Windows program** (`Program.cs`, run with `dotnet run`) that makes your PC act as a Bluetooth Low Energy touchscreen digitizer, using `GattServiceProvider`.

While it runs, it exposes a local TCP command interface on `127.0.0.1:8760`. Any other program or script connects there and sends absolute touch input, which WinBleTouch relays to the paired iPhone/iPad as BLE HID reports.

It uses the normal Windows Bluetooth stack, so there's no driver takeover and no exclusive control of the adapter. Each GATT service runs independently, and the HID service can start or stop without interrupting other Bluetooth connections.

`winbletouch.py` is a tiny Python client for that TCP interface; `examples/` is one complete consumer (mirror → mapping → touch). Neither is required — anything that can open a socket can drive it.

## Requirements

- Windows 10/11 with a Bluetooth adapter that supports the **LE peripheral role** (most built-in and USB BT 4.0+ adapters do; the probe below tells you).
- .NET SDK 10+ (project targets `net10.0-windows10.0.22621.0`).
- An iPhone/iPad to pair with. AssistiveTouch is **not** required.

## Scope

Note this project is the **BLE HID transport only**. Its entire public surface:

| Call | Meaning |
|---|---|
| `contact(x, y)` | send/update the active absolute contact |
| `release()` | release the active contact |

**Coordinate contract:** `x`, `y` are absolute HID coordinates in `0..10000` (`0,0` = top-left of the digitizer surface, `10000,10000` = bottom-right); out-of-range is clamped.

Taps, holds, drags, freehand strokes, gesture recognition, and app logic are all yours — the library only streams contacts. A tap is `contact` then `release`; a drag is `contact` repeatedly then `release`.

## Make your own mapper

I myself don't know what mirroring/streaming wrapper you're using, be it:

**USB**: iPhoneMirror / IosScreenCaptureTool
**Wireless**: UxPlay / any AirPlay receiver
**Commercial/easy**: AirDroid Cast

Coordinate mapping is specific to that integration, and it's simple arithmetic: once you know the iOS screen bounds and the rectangle where the video is rendered, converting pointer coordinates into the `0..10000` HID range is straightforward. Whatever you use is responsible for that conversion.

`examples/mirror_consumer.py` shows my way (`Mapper`: crop → rotate → normalize) — copy or adapt it; it is not part of the library.

## Main Files

| File | Role |
|---|---|
| `Program.cs` | The component. GATT server + `contact`/`release`, exposed on a loopback control endpoint (`127.0.0.1:8760`, env `WINBLETOUCH_PORT`). Line protocol: `contact <x> <y>` / `release` / `status` / `ping`. |
| `winbletouch.py` | Minimal Python client — `WinTouch.contact(x, y)` / `.release()`. Import it, or use it as a template for a client in another language. |


## Run

```bash
dotnet run -c Release
```

On the iPhone/iPad: Settings > Bluetooth > pick this PC > pair. **Accept the pairing prompt on *both* the PC and the iPhone** — Windows shows one too and it's easy to miss; if you only confirm on the phone, iOS connects but never subscribes.

Console should show `host SUBSCRIBED to input report`. Then drive it:

```bash
python winbletouch.py
```

Or from a headless run (`dotnet run` with redirected stdin), stream `contact`/`release` lines to the control endpoint.

### Probe mode

Runs setup only and prints one `[PROBE RESULT]` verdict (also the exit code):

```powershell
$env:WINBLETOUCH_PROBE=1; dotnet run -c Release # PowerShell
```
```bash
WINBLETOUCH_PROBE=1 dotnet run -c Release # bash
```


| Verdict | exit | Meaning |
|---|---|---|
| `NO_ADAPTER` | 2 | `BluetoothAdapter.GetDefaultAsync()` null — hardware/driver problem. Nothing about HID policy. |
| `NO_PERIPHERAL_ROLE` | 4 | Adapter present, can't be a BLE peripheral. |
| `HID_0x1812_DISABLED_BY_POLICY` | 3 | Windows blocks the HID service on this stack. |
| `HID_0x1812_PUBLISHED` | 0 | **Success.** |

## Findings

Tested on Windows 11 + iOS, latest August 2026:

- `GattServiceProvider.CreateAsync(0x1812)` returns **Success** — HID is not policy-blocked. (Windows reserves DIS / GATT / GAP / Scan Parameters, not HID.)
- A fresh iPhone pairing reads HID Information + Report Map and **subscribes to the Input Report on the first connection**; a 5-byte absolute report yields a **real iOS touch** at the sent coordinates.
- **AssistiveTouch is not required.** Missing DIS / PnP ID / GAP appearance did not block iOS.
- `GattServiceProvider` gives no control over advertising flags / appearance / bonding parameters; pairing + encryption are OS-driven when iOS first reads the encrypted Input Report. One transient `[adv] Aborted` before `Started` is normal.
- Reserved-service behaviour and peripheral-role support are stack/adapter dependent — run the probe on your hardware.

## Optional future helpers (would ship separately, not in the core)

- In-range hover (`0x02`) as a distinct call.
- A `Mapper`-style helper library for common preview geometries.


## Implementation use case example (not the library)

| File | Role |
|---|---|
| `examples/mirror_consumer.py` | `Mapper` (preview px -> `0..10000`) + live-stream forwarding + `box`/`spiral` demos. |
| `examples/draw_on_mirror.py` | Live: shows the iPhone mirror in a window, left-drag draws into whatever app is open on the phone. Owns its own window-px -> `0..10000` mapping. Needs `opencv-python`, `numpy`, the iPhoneMirror window. |
| `examples/_mirror.py` | Helper: captures the **full** iPhone display (no status-bar/home-indicator inset) so a plain normalize is exact. |
| `examples/calibrate_mirror.py` | Helper: 2-point affine calibration for stream sources where you *can't* get the full-screen rect (letterboxed video, cropped share). Not needed for the mirror. |
| `examples/test_mapper.py` | Layer test for the example `Mapper` (no BLE). `python examples/test_mapper.py`. |



----

Generative AI was used to correct the grammar in the content.