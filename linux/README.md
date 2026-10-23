# iOSBleDigitizer — Linux

Native Python/BlueZ port of `digitizer.py`. Runs on x86/x86_64 as well as ARM boards. 
Same BLE HID multitouch digitizer, same `127.0.0.1:8760` control protocol as the Windows version, see the root [README](../README.md#raw-api) for the protocol itself. This file only covers what's specific to running it on Linux.

Tested on: Fedora (`x86_64`) and (`linux-armv7`) board.

## Requirements

- BlueZ 5.48+ (`bluez` package, provides `bluetoothd`, `bluetoothctl`, `btmgmt`)
- Python 3.10+, `python3-dbus`, `python3-gi`
- Bluetooth adapter with LE **peripheral** role + **advertising** support

Classic BT discovery/pairing (central-role use, e.g. scanning for headphones) does **not** imply the adapter can act as a peripheral and host a GATTserver, that's a separate capability some cheap combo chips lack. Check before relying on it:

```bash
sudo btmgmt info
```

Look for `le` and `advertising` in the `supported settings` line.

## Run

```bash
sudo python3 digitizer.py -v
```

`sudo` is needed for the BlueZ GATT peripheral D-Bus APIs. `bluetoothd--experimental` is **not** required on modern BlueZ (5.6x+), the GATT-manager/advertising D-Bus interfaces are stable there, not experimental, despite the comment in some older references.

## Probe

Setup-only smoke test publishes the service and exits without entering the control loop:

```bash
sudo python3 digitizer.py --probe -v
```

Expect:

```text
[PROBE RESULT] HID_0x1812_PUBLISHED
```

## Options

| Flag / env var              | Meaning                                              |
|------------------------------|-------------------------------------------------------|
| `-p`, `--port <n>`           | TCP control port (default `8760`)                    |
| `--adapter <path>`           | BlueZ adapter object path (auto-detected otherwise)  |
| `-v`, `--verbose`            | Debug logging                                         |
| `--probe`                    | Setup only, exit without the control loop             |
| `DIGITIZER_CONTACTS`         | Number of multitouch contact slots advertised (default `10`) |
| `DIGITIZER_PORT`             | Overrides `-p`                                        |

## Pairing

Pair the host from the iPhone's Bluetooth settings and accept the prompt. Wait for:

```text
host SUBSCRIBED to input report
```

## LE-only mode

On startup the script disables BR/EDR on the adapter (`set_le_only`) so iOS connects over BLE only, and restores BR/EDR on clean shutdown.

This forces a brief internal power cycle of the adapter — expect a short blip at start/stop, not a hang.

