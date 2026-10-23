#!/usr/bin/env python3
"""Python client for Digitizer. Coordinates are 0..10000."""
from __future__ import annotations
import socket

HOST, PORT = "127.0.0.1", 8760


class BleTouch:
    def __init__(self, host: str = HOST, port: int = PORT, timeout: float = 5.0):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.f = self.sock.makefile("rwb", buffering=0)

    def _cmd(self, line: str) -> str:
        self.f.write((line + "\n").encode("ascii"))
        return self.f.readline().decode().strip()

    def contact(self, x: float, y: float, *, id: int = 0) -> str:
        return self._cmd(f"contact {id} {int(round(x))} {int(round(y))}")

    def send(self) -> str:
        return self._cmd("send")

    def release(self, id: int | None = None) -> str:
        return self._cmd("release" if id is None else f"release {id}")

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

    def __enter__(self) -> "BleTouch":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
