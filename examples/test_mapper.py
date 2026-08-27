#!/usr/bin/env python3
"""Layer test for the example Mapper only — no BLE, no control endpoint.

Feed synthetic preview coordinates in, assert the 0..10000 output.
Run:  python examples/test_mapper.py
"""
from mirror_consumer import Mapper


def approx(a, b, tol=1.0):
    return abs(a - b) <= tol


def check(name, got, want):
    (gx, gy), (wx, wy) = got, want
    ok = approx(gx, wx) and approx(gy, wy)
    print(f"{'PASS' if ok else 'FAIL'}  {name:32}  got=({gx:.1f},{gy:.1f})  want=({wx},{wy})")
    return ok


def main() -> int:
    ok = True

    # 1. Plain normalize, no crop, no rotation.
    m = Mapper(400, 800)
    ok &= check("origin", m.to_hid(0, 0), (0, 0))
    ok &= check("center", m.to_hid(200, 400), (5000, 5000))
    ok &= check("far corner", m.to_hid(400, 800), (10000, 10000))
    ok &= check("clamp beyond", m.to_hid(500, -20), (10000, 0))

    # 2. Letterbox crop: 40px bars left/right, 100px top/bottom.
    m = Mapper(480, 1000, crop=(40, 100, 440, 900))
    ok &= check("crop top-left -> 0,0", m.to_hid(40, 100), (0, 0))
    ok &= check("crop center", m.to_hid(240, 500), (5000, 5000))
    ok &= check("crop bottom-right", m.to_hid(440, 900), (10000, 10000))

    # 3. Rotation (source -> device), unit square.
    ok &= check("rot90 of (0,0)", Mapper(100, 100, rotate=90).to_hid(0, 0), (0, 10000))
    ok &= check("rot90 of (100,0)", Mapper(100, 100, rotate=90).to_hid(100, 0), (0, 0))
    ok &= check("rot180 of (0,0)", Mapper(100, 100, rotate=180).to_hid(0, 0), (10000, 10000))
    ok &= check("rot270 of (0,0)", Mapper(100, 100, rotate=270).to_hid(0, 0), (10000, 0))

    print("\n" + ("ALL PASS" if ok else "SOME FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
