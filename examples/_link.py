"""Fire-and-forget link to the WinBleTouch service, for the interactive examples.

`winbletouch.WinTouch` waits for the "ok" reply on every call — fine for scripts,
bad on a UI thread where a fast drag issues ~60 `contact`s/second and each
blocking round-trip stalls rendering. This sends without waiting; a drain thread
discards replies so the service's writes never back up. Same wire protocol.
"""
from __future__ import annotations
import socket
import threading

HOST, PORT = "127.0.0.1", 8760


class Sender:
    def __init__(self, host: str = HOST, port: int = PORT):
        self.sock = socket.create_connection((host, port), timeout=5)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        threading.Thread(target=self._drain, daemon=True).start()

    def _drain(self) -> None:
        try:
            for _ in self.sock.makefile("rb"):
                pass
        except OSError:
            pass

    def _send(self, line: str) -> None:
        try:
            self.sock.sendall((line + "\n").encode("ascii"))
        except OSError:
            pass

    def contact(self, x: float, y: float) -> None:
        self._send(f"contact {int(round(x))} {int(round(y))}")

    def release(self) -> None:
        self._send("release")

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass
