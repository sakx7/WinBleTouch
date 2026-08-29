# Research notes

## Findings

- `GattServiceProvider.CreateAsync(0x1812)` returns Success. Windows doesn't
  policy-block HID; it reserves DIS, GATT, GAP and Scan Parameters, not the HID
  service.
- A fresh iPhone pairing reads HID Information and the Report Map and subscribes to
  the Input Report on the first connection. No reconnect trick needed.
- With Zoom on, a 5-byte absolute stylus report lands as a real touch at the sent
  coordinates. Holds, a 3x3 grid and drags all mapped to within one physical pixel
  on iOS 18.6.2 and 26.6. With Zoom off, every tested iOS version ignores the same
  reports.
- `GattServiceProvider` exposes no control over advertising flags, appearance or
  bonding parameters. Pairing and encryption happen when iOS first reads the
  encrypted Input Report. One `[adv] Aborted` before `Started` is normal.
- Reserved-service behaviour and peripheral-role support depend on the adapter and
  stack. Run the probe on your own hardware.

