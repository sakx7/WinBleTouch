# iOSBleDigitizer

Cross-platform BLE HID absolute touch input for Windows and Linux.

Turns a PC into a Bluetooth LE digitizer for an IOS (iPhone or iPad).

Touches use absolute coordinates, no cursor, no bs assistive touch and multiple simultaneous contacts is supported.

Both platforms advertise the same HID 0x1812 multitouch descriptor and expose the same `127.0.0.1:8760` control protocol, so anything built against one backend works unchanged against the other.

- [`windows/`](windows/) — native .NET/WinRT implementation -> [README]()
- [`linux/`](linux/) — native Python/BlueZ implementation -> [README](linux/README.md)

## Requirements

- **Windows**: Windows 10/11 with a Bluetooth adapter that supports the LE peripheral role, .NET SDK 10+
- **Linux**: BlueZ 5.48+, Python 3.10+, `python3-dbus`, `python3-gi`
- iPhone or iPad with Accessibility > Zoom enabled

    <sub>Zoom's `ZWEventProcessor` sets a HID event filter mask of `0x13` (**digitizer**, keyboard, mouse).</sub>

    - Set Zoom to 1x magnification, essentially 0 so the normal screen (search up if your stuck).


## Run

### Windows

```powershell
cd windows
dotnet run -c Release
```

### Linux

```bash
cd linux
sudo python3 digitizer.py -v
```

See [linux/README.md](linux/README.md) for Linux-specific requirements, probing, and pairing notes.

Pair the host in iOS Bluetooth settings and accept the pairing prompt on both devices. Wait for:

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
from touch_client import BleTouch

with BleTouch() as t:
    t.contact(5000, 5000)   # stage contact 0
    t.send()                 # deliver to device
    t.release()              # lift
```

Interactive examples and multitouch demos: [examples.ipynb](examples.ipynb).

You are encouraged to build custom applications on top of this framework (such as a live mapper). The given Python client translates raw contact and release events into system wide actions.

If iOS has cached an older report map, forget PC in Bluetooth settings and pair again.

## Probe

```powershell
# Windows (run from windows/)
$env:DIGITIZER_PROBE=1; dotnet run -c Release
```

```bash
# Linux (run from linux/)
sudo python3 digitizer.py --probe -v
```

## TODO

- [ ] Findings writeup 
- [ ] latency/timing data

## License

MIT. See [LICENSE](LICENSE).
