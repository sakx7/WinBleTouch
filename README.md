# WinBleTouch

Use a Windows PC as a Bluetooth LE digitizer for an iPhone or iPad. Touches use absolute coordinates with no cursor.

## Requirements

- Windows 10/11 with a Bluetooth adapter that supports the LE peripheral role
- .NET SDK 10+
- iPhone or iPad with Accessibility > Zoom enabled

Set Zoom to Full Screen, 1x magnification, with the Zoom Controller off.

## Run

```powershell
dotnet run -c Release
```

Pair PC in iOS Bluetooth settings and accept the pairing prompt on both devices. Wait for:

```text
host SUBSCRIBED to input report
```


## Raw API

The service listens on `127.0.0.1:8760`:

```text
contact <x> <y>
release
```

Coordinates range from `0..10000`, with `(0,0)` at the top-left and `(10000,10000)` at the bottom-right. Stream `contact` updates for a drag, then call `release`.

Python usage:

```python
from winbletouch import WinTouch

with WinTouch() as touch:
    touch.contact(5000, 5000)
    touch.release()
```

You are encouraged to build custom applications on top of this framework (such as a live mapper). The given Python client translates raw contact and release events into system wide actions. 

If iOS has cached an older report map, forget PC in Bluetooth settings and pair again.

## Probe

```powershell
$env:WINBLETOUCH_PROBE=1; dotnet run -c Release
```

## TODO

- [ ] Findings writeup 
- [ ] Potential Extended HID usage
- [ ] latency/timing data
- [ ] Linux/ARM port

## License

MIT. See [LICENSE](LICENSE).
