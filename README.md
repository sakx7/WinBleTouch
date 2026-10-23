# WinBleTouch

Use a Windows PC as a Bluetooth LE digitizer for an iPhone or iPad. Touches use absolute coordinates with no cursor.

## Requirements

- Windows 10/11 with a Bluetooth adapter that supports the LE peripheral role
- .NET SDK 10+
- iPhone or iPad with Accessibility > Zoom enabled

    <sub>Zoom's `ZWEventProcessor` sets a HID event filter mask of `0x13` (**digitizer, keyboard, mouse**).</sub>

    - Set Zoom to 1x magnification, default screen (search up if your stuck).


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
contact [id] <x> <y>    stage a touch (id defaults to 0)
send                     flush all staged contacts as one HID frame
release [id]             release one contact (or all if no id)
```

Coordinates range from `0..10000`, with `(0,0)` at the top-left and `(10000,10000)` at the bottom-right.

**Frame model:** `contact` stages state without sending. `send` packs all unreleased contacts into a single HID frame. Contacts persist until explicitly released, each frame is a complete snapshot of every active contact. How the receiving app interprets the input is application behaviour, not a protocol concern.

Python usage:

```python
from winbletouch import WinTouch

with WinTouch() as t:
    t.contact(5000, 5000)   # stage contact 0
    t.send()                 # deliver to device
    t.release()              # lift
```

Interactive examples and multitouch demos: [winbletouch.ipynb](winbletouch.ipynb).

You are encouraged to build custom applications on top of this framework (such as a live mapper). The given Python client translates raw contact and release events into system wide actions.

If iOS has cached an older report map, forget PC in Bluetooth settings and pair again.

## Probe

```powershell
$env:WINBLETOUCH_PROBE=1; dotnet run -c Release
```

## TODO

- [ ] Findings writeup 
- [ ] latency/timing data
- [ ] Linux/ARM port

## License

MIT. See [LICENSE](LICENSE).
