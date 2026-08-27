#!/usr/bin/env python3
"""Python client for the WinBleTouch BLE HID digitizer service.

Important: this script does NOT start the Bluetooth HID service itself.
Start the WinBleTouch Windows service first. This client then connects to
it locally on 127.0.0.1:8760 and sends touch commands.

Touch API:
    contact(x, y)  - touch down at a position, or move the active touch
    release()      - lift the active touch

Coordinates are absolute HID coordinates from 0..10000:
    (0, 0)           = top-left
    (5000, 5000)     = center
    (10000, 10000)   = bottom-right

Your own mirror, overlay, stream, automation script, or app must convert
its coordinates into this 0..10000 range. WinBleTouch does not perform
that mapping.
"""
from __future__ import annotations
import socket

HOST, PORT = "127.0.0.1", 8760


class WinTouch:
    def __init__(self, host: str = HOST, port: int = PORT, timeout: float = 5.0):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.f = self.sock.makefile("rwb", buffering=0)

    def _cmd(self, line: str) -> str:
        self.f.write((line + "\n").encode("ascii"))
        return self.f.readline().decode().strip()

    def contact(self, x: float, y: float) -> str:
        """Send/update the active absolute contact (0..10000). No active
        contact -> touch down there; active contact -> move it there."""
        return self._cmd(f"contact {int(round(x))} {int(round(y))}")

    def release(self) -> str:
        """Release the active contact."""
        return self._cmd("release")

    # operational plumbing, not touch semantics
    def status(self) -> str:
        return self._cmd("status")

    def ping(self) -> str:
        return self._cmd("ping")

    def close(self) -> None:
        try:
            self.f.close()
            self.sock.close()
        except OSError:
            pass

    def __enter__(self) -> "WinTouch":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


if __name__ == "__main__":
    # Tiny smoke test: single centre contact + release.
    with WinTouch() as t:
        print("status:", t.status())
        print("contact:", t.contact(5000, 5000))
        print("release:", t.release())
