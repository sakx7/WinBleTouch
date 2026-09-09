#!/usr/bin/env python3
"""Python client for WinBleTouch. Coordinates are 0..10000."""
from __future__ import annotations
import argparse
import socket
import time

HOST, PORT = "127.0.0.1", 8760


class WinTouch:
    def __init__(self, host: str = HOST, port: int = PORT, timeout: float = 5.0):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.f = self.sock.makefile("rwb", buffering=0)

    def _cmd(self, line: str) -> str:
        self.f.write((line + "\n").encode("ascii"))
        return self.f.readline().decode().strip()

    def contact(self, x: float, y: float) -> str:
        return self._cmd(f"contact {int(round(x))} {int(round(y))}")

    def release(self) -> str:
        return self._cmd("release")

    @staticmethod
    def _expect_ok(reply: str) -> str:
        if reply != "ok" and not reply.startswith("ok "):
            raise RuntimeError(reply or "WinBleTouch returned an empty response")
        return reply

    def tap(self, x: float, y: float, hold: float = 0.08) -> str:
        try:
            self._expect_ok(self.contact(x, y))
            if hold > 0:
                time.sleep(hold)
        finally:
            reply = self.release()
        return self._expect_ok(reply)

    def swipe(self, x1: float, y1: float, x2: float, y2: float,
              duration: float = 0.35, steps: int = 24,
              hold_before_release: float = 0.0) -> str:
        if steps < 1:
            raise ValueError("steps must be at least 1")
        if duration < 0 or hold_before_release < 0:
            raise ValueError("durations cannot be negative")

        step_delay = duration / steps
        try:
            for index in range(steps + 1):
                amount = index / steps
                x = x1 + (x2 - x1) * amount
                y = y1 + (y2 - y1) * amount
                self._expect_ok(self.contact(x, y))
                if index < steps and step_delay > 0:
                    time.sleep(step_delay)
            if hold_before_release > 0:
                time.sleep(hold_before_release)
        finally:
            reply = self.release()
        return self._expect_ok(reply)

    def _bottom_edge_swipe(self, end_y: int, *, down_hold: float,
                           step_delay: float, body_steps: int,
                           hold_before_release: float = 0.0) -> str:
        try:
            self._expect_ok(self.contact(5000, 10000))
            time.sleep(down_hold)

            for y in range(9980, 9799, -20):
                self._expect_ok(self.contact(5000, y))
                time.sleep(step_delay)

            for index in range(1, body_steps + 1):
                amount = index / body_steps
                y = 9800 + (end_y - 9800) * amount
                self._expect_ok(self.contact(5000, y))
                time.sleep(step_delay)

            if hold_before_release > 0:
                time.sleep(hold_before_release)
        finally:
            reply = self.release()
        return self._expect_ok(reply)

    def home(self) -> str:
        return self._bottom_edge_swipe(5500, down_hold=0.04,
                                       step_delay=0.008, body_steps=17)

    def app_switcher(self) -> str:
        return self._bottom_edge_swipe(7000, down_hold=0.08,
                                       step_delay=0.016, body_steps=28,
                                       hold_before_release=0.80)

    def notification_center(self) -> str:
        return self.swipe(2000, 0, 2000, 8000, duration=0.40, steps=25) # is a bit aggressive, but works
    def control_center(self) -> str:
        return self.swipe(8000, 0, 8000, 8000, duration=0.40, steps=25) # same here 

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


def _run_smoke_test(t: WinTouch) -> None:
    print("status:", t.status())
    print("centre tap:", t.tap(5000, 5000))
    time.sleep(0.5)

    tests = (
        ("app switcher", t.app_switcher),
        ("back to app", lambda: t.tap(5000, 5000)),
        ("home", t.home),
        ("notification center", t.notification_center),
        ("close notification center", t.home),
        ("control center", t.control_center),
        ("close control center", t.home),
    )
    for label, command in tests:
        print(f"{label}:", command())
        time.sleep(1.0)


def main() -> None:
    parser = argparse.ArgumentParser(description="WinBleTouch client")
    parser.add_argument(
        "command", nargs="?", default="smoke",
        choices=("smoke", "home", "app-switcher", "notification-center", "control-center"),
        help="Python command to run (default: smoke)",
    )
    args = parser.parse_args()

    with WinTouch() as t:
        if args.command == "smoke":
            _run_smoke_test(t)
        else:
            command = getattr(t, args.command.replace("-", "_"))
            print(f"{args.command}:", command())


if __name__ == "__main__":
    main()
