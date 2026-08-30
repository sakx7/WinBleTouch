# WinBleTouch

Turn a Windows PC into a Bluetooth LE touchscreen for an iPhone or iPad. It sends absolute touch coordinates over BLE HID as a real digitizer, not a mouse, so there's no cursor and no AssistiveTouch.

`Program.cs` is a .NET program you run with `dotnet run`. While it's running it listens on `127.0.0.1:8760`; anything that can open a socket sends `contact <x> <y>` / `release` and it relays those to the paired phone as HID reports.

It uses the normal Windows Bluetooth stack. No driver takeover and no exclusive hold on the adapter. The HID service can start and stop without dropping other Bluetooth connections.

`winbletouch.py` is a small Python client for that socket. You don't need it.

## Requirements

- Windows 10/11 with a Bluetooth adapter that supports the LE peripheral role (most BT 4.0+ adapters do; the probe below checks).
- .NET SDK 10+ (project targets `net10.0-windows10.0.22621.0`).
- An iPhone or iPad with **Accessibility > Zoom** turned on (see below).

## iOS setup

iOS ignores reports from an unpaired BLE digitizer unless an accessibility touch path is running. Zoom is the easiest one to switch on:

1. Settings > Accessibility > Zoom > **On**.
2. Zoom Region: Full Screen. Zoom Filter: None. Zoom Controller: Off. These are already the defaults.
3. The screen magnifies at first. Triple-tap with three fingers to open the Zoom menu, then drag the magnification slider all the way down to 1x. At 1x there's no lens, no button, no cursor, and coordinates map 1:1.

Turning Zoom back off stops touches from coming through. AssistiveTouch is not involved.

Tested on stock, non-jailbroken iOS 17 (iPhone 13), 18.6.2 (iPhone 14) and 26.6 (iPhone 16 Pro Max), Developer Mode on and off.

## API

Two calls:

| Call | Meaning |
|---|---|
| `contact(x, y)` | put down, or move, the active contact |
| `release()` | lift it |

`x` and `y` are `0..10000` across the digitizer surface (`0,0` = top-left, `10000,10000` = bottom-right). Out of range is clamped.

A tap is `contact` then `release`. A drag is `contact` a few times then `release`. One contact at a time, no multi-touch. Taps, holds, gestures, and screen-to-HID coordinate mapping are your job, not the library's.

## Run

```bash
dotnet run -c Release
```

Do the iOS setup, then pair from Settings > Bluetooth. Confirm the pairing prompt **on both the PC and the phone** 
Windows shows its own it's easy to miss; if u skip it, iOS connects but never subscribes.

Wait for `host SUBSCRIBED to input report`, then:

```bash
python winbletouch.py
```

Or pipe `contact` / `release` lines to `127.0.0.1:8760` from anything.

### Probe

Checks setup and prints one verdict (also the exit code):

```bash
WINBLETOUCH_PROBE=1 dotnet run -c Release
```
```powershell
$env:WINBLETOUCH_PROBE=1; dotnet run -c Release
```

| Verdict | exit | Meaning |
|---|---|---|
| `HID_0x1812_PUBLISHED` | 0 | works |
| `NO_ADAPTER` | 2 | no adapter, or a driver problem |
| `HID_0x1812_DISABLED_BY_POLICY` | 3 | Windows blocks the HID service on this stack |
| `NO_PERIPHERAL_ROLE` | 4 | adapter can't act as a BLE peripheral |

## Files

| File | |
|---|---|
| `Program.cs` | the program: GATT server plus the socket on `127.0.0.1:8760` (set `WINBLETOUCH_PORT` to change it). Line protocol: `contact <x> <y>` / `release` / `status` / `ping`. |
| `winbletouch.py` | Python client — `WinTouch.contact(x, y)` / `.release()`. Or copy it as a starting point for another language. |

## Notes

- Windows reserves DIS / GATT / GAP / Scan Parameters, so PnP ID and appearance can't be set from `GattServiceProvider`. iOS doesn't need them.
- Connection interval is negotiated by iOS and isn't exposed here, so latency has to be chased in firmware and hardware.
- One `[adv] Aborted` before `Started` at startup is normal.

## Ideas, not built

- Hover (`0x02`) as its own call.
- A coordinate-mapping helper for common mirror layouts.

## License

MIT. See [LICENSE](LICENSE).
