## Findings

- `GattServiceProvider.CreateAsync(0x1812)` returns **Success** — HID is not policy-blocked. (Windows reserves DIS / GATT / GAP / Scan Parameters, not HID.)
- A fresh iPhone pairing reads HID Information + Report Map and **subscribes to the Input Report on the first connection**.
- With **Zoom on**, a 5-byte absolute stylus report is delivered to the foreground app as a real touch at the sent coordinates — holds, a 3×3 screen grid, and drags all landed within one physical pixel of the expected mapping on iOS 18.6.2 and 26.6. With **Zoom off**, all tested iOS versions ignore the same reports.
- `GattServiceProvider` gives no control over advertising flags / appearance / bonding parameters; pairing + encryption are OS-driven when iOS first reads the encrypted Input Report. One transient `[adv] Aborted` before `Started` is normal.
- Reserved-service behaviour and peripheral-role support are stack/adapter dependent — run the probe on your hardware.