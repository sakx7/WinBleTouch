#!/usr/bin/env python3
"""
Native Python/BlueZ backend for Digitizer.

Advertises a BLE HID multitouch touchscreen digitizer (service 0x1812) via
BlueZ D-Bus, exposing the same TCP control protocol on 127.0.0.1:8760 as the
Windows/.NET version. Requires BlueZ 5.48+ — the GATT-manager/advertising
D-Bus interfaces are stable there, no --experimental flag needed.

Loopback endpoint primitives (mirrors Program.cs):
    contact <x> <y>           Set contact id=0 position (0..10000)
    contact <id> <x> <y>      Set specific contact id position
    send                      Flush all contacts as one HID frame
    release                   Release all contacts
    release <id>              Release one contact
Re-pair the iPhone after updating — the descriptor has changed.

Run:
    sudo python3 digitizer.py
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import sys
from typing import Any

import dbus
import dbus.exceptions
import dbus.mainloop.glib
import dbus.service
from gi.repository import GLib

log = logging.getLogger("bletouch")

BLUEZ_BUS = "org.bluez"
ADAPTER_IFACE = "org.bluez.Adapter1"
LE_ADV_MANAGER_IFACE = "org.bluez.LEAdvertisingManager1"
LE_ADV_IFACE = "org.bluez.LEAdvertisement1"
GATT_MANAGER_IFACE = "org.bluez.GattManager1"
GATT_SERVICE_IFACE = "org.bluez.GattService1"
GATT_CHRC_IFACE = "org.bluez.GattCharacteristic1"
GATT_DESC_IFACE = "org.bluez.GattDescriptor1"
DBUS_OM_IFACE = "org.freedesktop.DBus.ObjectManager"
DBUS_PROP_IFACE = "org.freedesktop.DBus.Properties"

AGENT_IFACE = "org.bluez.Agent1"
AGENT_MANAGER_IFACE = "org.bluez.AgentManager1"

# Compact multitouch descriptor — same construction as BuildReportDescriptor()
# in Program.cs. Push/Pop shares globals across N contact collections. Each
# contact: Tip+Confidence flags, Contact ID, X, Y. Confidence is needed on
# Apple hosts: without it IOHIDEventDriver classifies a Finger collection as
# a Hand, blocking SpringBoard's edge recognizers.
INPUT_REPORT_ID = 0x01
FEATURE_REPORT_ID = 0x02
CONTACT_BYTES = 6
CONTACT_SLOTS = max(1, int(os.environ.get("DIGITIZER_CONTACTS", "10")))
INPUT_REPORT_LENGTH = CONTACT_SLOTS * CONTACT_BYTES + 1


def _build_report_descriptor() -> bytes:
    header = bytes([
        0x05, 0x0D,                         # Usage Page (Digitizer)
        0x09, 0x04,                         # Usage (Touch Screen)
        0xA1, 0x01,                         # Collection (Application)
        0x85, INPUT_REPORT_ID,              # Report ID (1)
        0x15, 0x00, 0x25, 0x01,             # Logical Min 0, Max 1
        0x35, 0x00, 0x45, 0x00,             # Physical Min 0, Max 0
        0x55, 0x00, 0x65, 0x00,             # Unit Exponent 0, Unit None
        0x75, 0x01, 0x95, 0x02,             # Report Size 1, Count 2
    ])
    contact = bytes([
        0xA4,                               # Push (save globals)
        0x09, 0x22, 0xA1, 0x02,             # Finger, Logical Collection
        0x09, 0x42, 0x09, 0x47, 0x81, 0x02, # Tip Switch + Confidence, Input
        0x95, 0x06, 0x81, 0x03,             # 6-bit padding
        0x09, 0x51, 0x25, 0x7F,             # Contact ID, Max 127
        0x75, 0x08, 0x95, 0x01, 0x81, 0x02, # 8-bit, Input
        0x05, 0x01, 0x09, 0x01, 0xA1, 0x00, # Generic Desktop, Pointer, Physical Col
        0x09, 0x30, 0x09, 0x31,             # X, Y
        0x26, 0x10, 0x27,                   # Logical Max 10000
        0x75, 0x10, 0x95, 0x02, 0x81, 0x02, # 16-bit x 2, Input
        0xC0,                               # End Pointer
        0xB4, 0xC0,                         # Pop, End Finger
    ])
    tail = bytes([
        0x09, 0x54,                         # Usage (Contact Count)
        0x25, CONTACT_SLOTS & 0xFF,         # Logical Max
        0x75, 0x08, 0x95, 0x01, 0x81, 0x02, # 8-bit, Input
        0x85, FEATURE_REPORT_ID,            # Report ID (2)
        0x09, 0x55,                         # Usage (Contact Count Maximum)
        0xB1, 0x02,                         # Feature
        0xC0,                               # End Application
    ])
    return header + contact * CONTACT_SLOTS + tail


REPORT_MAP = _build_report_descriptor()

HID_INFO = bytes([0x11, 0x01, 0x00, 0x01])  # bcdHID 1.11, country 0, flags RemoteWake

OBJ_BASE = "/com/digitizer"


# ---------------------------------------------------------------------------
# D-Bus GATT objects
# ---------------------------------------------------------------------------

class Application(dbus.service.Object):
    """Root object that implements ObjectManager for BlueZ GattManager1."""

    def __init__(self, bus: dbus.SystemBus):
        self.path = OBJ_BASE
        self.services: list[Service] = []
        super().__init__(bus, self.path)

    def add_service(self, service: Service) -> None:
        self.services.append(service)

    @dbus.service.method(DBUS_OM_IFACE, out_signature="a{oa{sa{sv}}}")
    def GetManagedObjects(self) -> dict:
        objs: dict[str, dict[str, dict[str, Any]]] = {}
        for svc in self.services:
            objs[svc.path] = svc.get_properties()
            for chrc in svc.characteristics:
                objs[chrc.path] = chrc.get_properties()
                for desc in chrc.descriptors:
                    objs[desc.path] = desc.get_properties()
        return objs


class Service(dbus.service.Object):
    def __init__(self, bus: dbus.SystemBus, index: int, uuid: str, primary: bool = True):
        self.path = f"{OBJ_BASE}/service{index}"
        self.uuid = uuid
        self.primary = primary
        self.characteristics: list[Characteristic] = []
        super().__init__(bus, self.path)

    def add_characteristic(self, chrc: Characteristic) -> None:
        self.characteristics.append(chrc)

    def get_properties(self) -> dict:
        return {
            GATT_SERVICE_IFACE: {
                "UUID": self.uuid,
                "Primary": self.primary,
            }
        }

    @dbus.service.method(DBUS_PROP_IFACE, in_signature="ss", out_signature="v")
    def Get(self, iface: str, prop: str) -> Any:
        props = self.get_properties().get(iface)
        if props and prop in props:
            return props[prop]
        raise dbus.exceptions.DBusException("org.freedesktop.DBus.Error.InvalidArgs")

    @dbus.service.method(DBUS_PROP_IFACE, in_signature="s", out_signature="a{sv}")
    def GetAll(self, iface: str) -> dict:
        if iface == GATT_SERVICE_IFACE:
            return self.get_properties()[GATT_SERVICE_IFACE]
        raise dbus.exceptions.DBusException("org.freedesktop.DBus.Error.InvalidArgs")


class Characteristic(dbus.service.Object):
    def __init__(self, bus: dbus.SystemBus, index: int, uuid: str,
                 flags: list[str], service: Service):
        self.path = f"{service.path}/char{index}"
        self.uuid = uuid
        self.flags = flags
        self.service = service
        self.descriptors: list[Descriptor] = []
        self.notifying = False
        super().__init__(bus, self.path)

    def add_descriptor(self, desc: Descriptor) -> None:
        self.descriptors.append(desc)

    def get_properties(self) -> dict:
        return {
            GATT_CHRC_IFACE: {
                "Service": dbus.ObjectPath(self.service.path),
                "UUID": self.uuid,
                "Flags": dbus.Array(self.flags, signature="s"),
            }
        }

    @dbus.service.method(DBUS_PROP_IFACE, in_signature="ss", out_signature="v")
    def Get(self, iface: str, prop: str) -> Any:
        props = self.get_properties().get(iface)
        if props and prop in props:
            return props[prop]
        raise dbus.exceptions.DBusException("org.freedesktop.DBus.Error.InvalidArgs")

    @dbus.service.method(DBUS_PROP_IFACE, in_signature="s", out_signature="a{sv}")
    def GetAll(self, iface: str) -> dict:
        if iface == GATT_CHRC_IFACE:
            return self.get_properties()[GATT_CHRC_IFACE]
        raise dbus.exceptions.DBusException("org.freedesktop.DBus.Error.InvalidArgs")

    @dbus.service.method(GATT_CHRC_IFACE, in_signature="a{sv}", out_signature="ay")
    def ReadValue(self, options: dict) -> dbus.Array:
        raise dbus.exceptions.DBusException(
            "org.bluez.Error.NotSupported", "ReadValue not implemented")

    @dbus.service.method(GATT_CHRC_IFACE, in_signature="aya{sv}")
    def WriteValue(self, value: list, options: dict) -> None:
        raise dbus.exceptions.DBusException(
            "org.bluez.Error.NotSupported", "WriteValue not implemented")

    @dbus.service.method(GATT_CHRC_IFACE)
    def StartNotify(self) -> None:
        self.notifying = True
        log.info("host SUBSCRIBED to input report")

    @dbus.service.method(GATT_CHRC_IFACE)
    def StopNotify(self) -> None:
        self.notifying = False
        log.info("host unsubscribed from input report")

    @dbus.service.signal(DBUS_PROP_IFACE, signature="sa{sv}as")
    def PropertiesChanged(self, iface: str, changed: dict, invalidated: list) -> None:
        pass


class Descriptor(dbus.service.Object):
    def __init__(self, bus: dbus.SystemBus, index: int, uuid: str,
                 flags: list[str], characteristic: Characteristic):
        self.path = f"{characteristic.path}/desc{index}"
        self.uuid = uuid
        self.flags = flags
        self.characteristic = characteristic
        super().__init__(bus, self.path)

    def get_properties(self) -> dict:
        return {
            GATT_DESC_IFACE: {
                "Characteristic": dbus.ObjectPath(self.characteristic.path),
                "UUID": self.uuid,
                "Flags": dbus.Array(self.flags, signature="s"),
            }
        }

    @dbus.service.method(DBUS_PROP_IFACE, in_signature="ss", out_signature="v")
    def Get(self, iface: str, prop: str) -> Any:
        props = self.get_properties().get(iface)
        if props and prop in props:
            return props[prop]
        raise dbus.exceptions.DBusException("org.freedesktop.DBus.Error.InvalidArgs")

    @dbus.service.method(DBUS_PROP_IFACE, in_signature="s", out_signature="a{sv}")
    def GetAll(self, iface: str) -> dict:
        if iface == GATT_DESC_IFACE:
            return self.get_properties()[GATT_DESC_IFACE]
        raise dbus.exceptions.DBusException("org.freedesktop.DBus.Error.InvalidArgs")

    @dbus.service.method(GATT_DESC_IFACE, in_signature="a{sv}", out_signature="ay")
    def ReadValue(self, options: dict) -> dbus.Array:
        raise dbus.exceptions.DBusException(
            "org.bluez.Error.NotSupported", "ReadValue not implemented")


# ---------------------------------------------------------------------------
# Concrete HID characteristics and descriptors
# ---------------------------------------------------------------------------

class HidInfoCharacteristic(Characteristic):
    def __init__(self, bus: dbus.SystemBus, index: int, service: Service):
        super().__init__(bus, index, "00002a4a-0000-1000-8000-00805f9b34fb",
                         ["read"], service)

    @dbus.service.method(GATT_CHRC_IFACE, in_signature="a{sv}", out_signature="ay")
    def ReadValue(self, options: dict) -> dbus.Array:
        log.debug("HID Information read")
        return dbus.Array(HID_INFO, signature="y")


class ReportMapCharacteristic(Characteristic):
    def __init__(self, bus: dbus.SystemBus, index: int, service: Service):
        super().__init__(bus, index, "00002a4b-0000-1000-8000-00805f9b34fb",
                         ["read"], service)

    @dbus.service.method(GATT_CHRC_IFACE, in_signature="a{sv}", out_signature="ay")
    def ReadValue(self, options: dict) -> dbus.Array:
        log.debug("Report Map read (%d bytes)", len(REPORT_MAP))
        return dbus.Array(REPORT_MAP, signature="y")


class ControlPointCharacteristic(Characteristic):
    def __init__(self, bus: dbus.SystemBus, index: int, service: Service):
        super().__init__(bus, index, "00002a4c-0000-1000-8000-00805f9b34fb",
                         ["write-without-response"], service)

    @dbus.service.method(GATT_CHRC_IFACE, in_signature="aya{sv}")
    def WriteValue(self, value: list, options: dict) -> None:
        log.debug("HID Control Point written: %s", bytes(value).hex())


class ProtocolModeCharacteristic(Characteristic):
    def __init__(self, bus: dbus.SystemBus, index: int, service: Service):
        super().__init__(bus, index, "00002a4e-0000-1000-8000-00805f9b34fb",
                         ["read", "write-without-response"], service)
        self._mode = 0x01  # Report Protocol

    @dbus.service.method(GATT_CHRC_IFACE, in_signature="a{sv}", out_signature="ay")
    def ReadValue(self, options: dict) -> dbus.Array:
        log.debug("Protocol Mode read -> 0x%02x", self._mode)
        return dbus.Array([self._mode], signature="y")

    @dbus.service.method(GATT_CHRC_IFACE, in_signature="aya{sv}")
    def WriteValue(self, value: list, options: dict) -> None:
        if value:
            self._mode = value[0]
        log.debug("Protocol Mode written -> 0x%02x", self._mode)


class InputReportCharacteristic(Characteristic):
    def __init__(self, bus: dbus.SystemBus, index: int, service: Service):
        super().__init__(bus, index, "00002a4d-0000-1000-8000-00805f9b34fb",
                         ["read", "notify", "encrypt-read"], service)
        self._value = bytes(INPUT_REPORT_LENGTH)
        self._notify_fd: int | None = None
        self._notify_mtu: int = 23

    @dbus.service.method(GATT_CHRC_IFACE, in_signature="a{sv}", out_signature="ay")
    def ReadValue(self, options: dict) -> dbus.Array:
        log.debug("Input Report read by host")
        return dbus.Array(self._value, signature="y")

    @dbus.service.method(GATT_CHRC_IFACE, in_signature="a{sv}", out_signature="hq")
    def AcquireNotify(self, options: dict) -> tuple:
        mtu = int(options.get("mtu", 23))
        self._notify_mtu = mtu
        read_fd, write_fd = os.pipe()
        self._notify_fd = write_fd
        self.notifying = True
        log.info("host SUBSCRIBED to input report (AcquireNotify, mtu=%d)", mtu)
        return (dbus.types.UnixFd(read_fd), dbus.UInt16(mtu))

    @dbus.service.method(GATT_CHRC_IFACE)
    def StartNotify(self) -> None:
        self.notifying = True
        log.info("host SUBSCRIBED to input report (StartNotify)")

    @dbus.service.method(GATT_CHRC_IFACE)
    def StopNotify(self) -> None:
        self.notifying = False
        if self._notify_fd is not None:
            try:
                os.close(self._notify_fd)
            except OSError:
                pass
            self._notify_fd = None
        log.info("host unsubscribed from input report")

    def send_report(self, data: bytes) -> None:
        self._value = data
        if not self.notifying:
            return
        if self._notify_fd is not None:
            try:
                os.write(self._notify_fd, data)
                return
            except OSError as exc:
                log.warning("notify fd write failed: %s", exc)
                self._notify_fd = None
                self.notifying = False
                return
        GLib.idle_add(self._emit_notify, data)

    def _emit_notify(self, data: bytes) -> bool:
        self.PropertiesChanged(
            GATT_CHRC_IFACE,
            {"Value": dbus.Array(data, signature="y")},
            [],
        )
        return False


class FeatureReportCharacteristic(Characteristic):
    """Report Report Count Maximum — read/write, encrypted (report ID 2)."""
    def __init__(self, bus: dbus.SystemBus, index: int, service: Service):
        super().__init__(bus, index, "00002a4d-0000-1000-8000-00805f9b34fb",
                         ["read", "write", "encrypt-read", "encrypt-write"], service)
        self._value = min(CONTACT_SLOTS, 255)

    @dbus.service.method(GATT_CHRC_IFACE, in_signature="a{sv}", out_signature="ay")
    def ReadValue(self, options: dict) -> dbus.Array:
        log.debug("Feature Report read -> %d", self._value)
        return dbus.Array([self._value], signature="y")

    @dbus.service.method(GATT_CHRC_IFACE, in_signature="aya{sv}")
    def WriteValue(self, value: list, options: dict) -> None:
        if value:
            self._value = int(value[0])
        log.debug("Feature Report written -> %s", self._value)


class ReportReferenceDescriptor(Descriptor):
    """Report Reference: report ID + type (Input=0x01)."""
    def __init__(self, bus: dbus.SystemBus, index: int, chrc: Characteristic,
                 report_id: int, report_type: int):
        super().__init__(bus, index, "00002908-0000-1000-8000-00805f9b34fb",
                         ["read", "encrypt-read"], chrc)
        self._value = bytes([report_id, report_type])

    @dbus.service.method(GATT_DESC_IFACE, in_signature="a{sv}", out_signature="ay")
    def ReadValue(self, options: dict) -> dbus.Array:
        return dbus.Array(self._value, signature="y")


class BatteryLevelCharacteristic(Characteristic):
    def __init__(self, bus: dbus.SystemBus, index: int, service: Service):
        super().__init__(bus, index, "00002a19-0000-1000-8000-00805f9b34fb",
                         ["read"], service)

    @dbus.service.method(GATT_CHRC_IFACE, in_signature="a{sv}", out_signature="ay")
    def ReadValue(self, options: dict) -> dbus.Array:
        return dbus.Array([100], signature="y")


# Device Information Service characteristics

class PnPIdCharacteristic(Characteristic):
    # PnP ID: vendor source=0x02 (USB), vendor=0x05AC (Apple), product=0x0001, version=0x0001
    PNP_ID = bytes([0x02, 0xAC, 0x05, 0x01, 0x00, 0x01, 0x00])

    def __init__(self, bus: dbus.SystemBus, index: int, service: Service):
        super().__init__(bus, index, "00002a50-0000-1000-8000-00805f9b34fb",
                         ["read"], service)

    @dbus.service.method(GATT_CHRC_IFACE, in_signature="a{sv}", out_signature="ay")
    def ReadValue(self, options: dict) -> dbus.Array:
        log.debug("PnP ID read")
        return dbus.Array(self.PNP_ID, signature="y")


class ManufacturerNameCharacteristic(Characteristic):
    def __init__(self, bus: dbus.SystemBus, index: int, service: Service):
        super().__init__(bus, index, "00002a29-0000-1000-8000-00805f9b34fb",
                         ["read"], service)

    @dbus.service.method(GATT_CHRC_IFACE, in_signature="a{sv}", out_signature="ay")
    def ReadValue(self, options: dict) -> dbus.Array:
        return dbus.Array(b"BleTouch", signature="y")


# ---------------------------------------------------------------------------
# BLE advertisement
# ---------------------------------------------------------------------------

class HidAdvertisement(dbus.service.Object):
    def __init__(self, bus: dbus.SystemBus, index: int):
        self.path = f"{OBJ_BASE}/adv{index}"
        super().__init__(bus, self.path)

    @dbus.service.method(DBUS_PROP_IFACE, in_signature="s", out_signature="a{sv}")
    def GetAll(self, iface: str) -> dict:
        if iface != LE_ADV_IFACE:
            raise dbus.exceptions.DBusException("org.freedesktop.DBus.Error.InvalidArgs")
        return {
            "Type": "peripheral",
            "ServiceUUIDs": dbus.Array(["00001812-0000-1000-8000-00805f9b34fb"], signature="s"),
            "LocalName": dbus.String("BleTouch"),
            "Appearance": dbus.UInt16(0x03C2),  # HID Digitizer Tablet
            "Includes": dbus.Array(["tx-power"], signature="s"),
        }

    @dbus.service.method(LE_ADV_IFACE, in_signature="", out_signature="")
    def Release(self) -> None:
        log.info("advertisement released by BlueZ")


# ---------------------------------------------------------------------------
# Pairing agent (NoInputNoOutput for Just Works)
# ---------------------------------------------------------------------------

class PairingAgent(dbus.service.Object):
    CAPABILITY = "DisplayYesNo"

    def __init__(self, bus: dbus.SystemBus):
        self.path = f"{OBJ_BASE}/agent"
        super().__init__(bus, self.path)

    @dbus.service.method(AGENT_IFACE, in_signature="", out_signature="")
    def Release(self) -> None:
        log.info("agent released")

    @dbus.service.method(AGENT_IFACE, in_signature="os", out_signature="")
    def AuthorizeService(self, device: str, uuid: str) -> None:
        log.info("authorizing service %s for %s", uuid, device)

    @dbus.service.method(AGENT_IFACE, in_signature="o", out_signature="")
    def RequestAuthorization(self, device: str) -> None:
        log.info("auto-accepting pairing from %s", device)

    @dbus.service.method(AGENT_IFACE, in_signature="o", out_signature="s")
    def RequestPinCode(self, device: str) -> str:
        return "0000"

    @dbus.service.method(AGENT_IFACE, in_signature="o", out_signature="u")
    def RequestPasskey(self, device: str) -> dbus.UInt32:
        return dbus.UInt32(0)

    @dbus.service.method(AGENT_IFACE, in_signature="ouq", out_signature="")
    def DisplayPasskey(self, device: str, passkey: int, entered: int) -> None:
        log.info("passkey for %s: %06d (entered %d)", device, passkey, entered)

    @dbus.service.method(AGENT_IFACE, in_signature="ou", out_signature="")
    def RequestConfirmation(self, device: str, passkey: int) -> None:
        log.info("auto-confirming passkey %06d for %s (accept pairing prompt on iOS!)", passkey, device)

    @dbus.service.method(AGENT_IFACE, in_signature="", out_signature="")
    def Cancel(self) -> None:
        log.info("agent cancelled")


# ---------------------------------------------------------------------------
# Core digitizer logic
# ---------------------------------------------------------------------------

class Digitizer:
    """Multitouch frame state machine — mirrors the C# SetContact / FlushAsync /
    ReleaseAsync / ReleaseAllAsync. contact() only updates state; send() is
    required to transmit a frame, matching the breaking protocol change in
    Program.cs (commit f8bfefd)."""

    def __init__(self, input_chrc: InputReportCharacteristic):
        self._input = input_chrc
        # down flag + (x, y) per contact slot
        self._down = [False] * CONTACT_SLOTS
        self._x = [0] * CONTACT_SLOTS
        self._y = [0] * CONTACT_SLOTS
        self._lock = asyncio.Lock()

    @property
    def is_subscribed(self) -> bool:
        return self._input.notifying

    @staticmethod
    def _clamp(v: int) -> int:
        return max(0, min(10000, v))

    def _active_count(self) -> int:
        return sum(1 for d in self._down if d)

    def _active_summary(self) -> str:
        parts = [f"{i}:({self._x[i]},{self._y[i]})"
                 for i in range(CONTACT_SLOTS) if self._down[i]]
        return " ".join(parts) if parts else "none"

    def _build_frame(self) -> bytes:
        frame = bytearray(INPUT_REPORT_LENGTH)
        offset = 0
        count = 0
        for i in range(CONTACT_SLOTS):
            if self._down[i]:
                frame[offset] = 0x03  # Tip + Confidence
                frame[offset + 1] = i
                frame[offset + 2] = self._x[i] & 0xFF
                frame[offset + 3] = (self._x[i] >> 8) & 0xFF
                frame[offset + 4] = self._y[i] & 0xFF
                frame[offset + 5] = (self._y[i] >> 8) & 0xFF
                offset += CONTACT_BYTES
                count += 1
        frame[CONTACT_SLOTS * CONTACT_BYTES] = count
        return bytes(frame)

    def _build_release_frame(self, release_id: int = -1) -> bytes:
        frame = bytearray(INPUT_REPORT_LENGTH)
        offset = 0
        count = 0
        for i in range(CONTACT_SLOTS):
            if self._down[i]:
                releasing = release_id in (-1, i)
                frame[offset] = 0x02 if releasing else 0x03
                frame[offset + 1] = i
                frame[offset + 2] = self._x[i] & 0xFF
                frame[offset + 3] = (self._x[i] >> 8) & 0xFF
                frame[offset + 4] = self._y[i] & 0xFF
                frame[offset + 5] = (self._y[i] >> 8) & 0xFF
                offset += CONTACT_BYTES
                count += 1
        frame[CONTACT_SLOTS * CONTACT_BYTES] = count
        return bytes(frame)

    def set_contact(self, cid: int, x: int, y: int) -> tuple[int, int]:
        cx, cy = self._clamp(x), self._clamp(y)
        was_down = self._down[cid]
        self._down[cid] = True
        self._x[cid], self._y[cid] = cx, cy
        if not was_down:
            log.info("[touch] DOWN  id=%d (%d,%d)", cid, cx, cy)
        return cx, cy

    async def flush(self) -> str:
        async with self._lock:
            if not self.is_subscribed:
                raise RuntimeError("no HID client is subscribed")
            self._input.send_report(self._build_frame())
            return "ok"

    async def release(self, cid: int) -> str:
        async with self._lock:
            if not self._down[cid]:
                return "ok"
            if not self.is_subscribed:
                raise RuntimeError("no HID client is subscribed")
            x, y = self._x[cid], self._y[cid]
            self._input.send_report(self._build_release_frame(cid))
            self._down[cid] = False
            log.info("[touch] UP    id=%d (%d,%d)", cid, x, y)
            return "ok"

    async def release_all(self) -> str:
        async with self._lock:
            if not any(self._down):
                return "ok release-all"
            if not self.is_subscribed:
                raise RuntimeError("no HID client is subscribed")
            self._input.send_report(self._build_release_frame())
            released = self._active_count()
            for i in range(CONTACT_SLOTS):
                self._down[i] = False
                self._x[i] = self._y[i] = 0
            log.info("[touch] UP ALL (%d contacts)", released)
            return "ok release-all"

    async def status(self) -> str:
        return (f"ok subscribed={self.is_subscribed} "
                f"contacts={self._active_count()} "
                f"active={self._active_summary()}")

    async def execute(self, line: str) -> str:
        parts = line.replace(",", " ").split()
        if not parts:
            return "ok"
        cmd = parts[0].lower()
        if cmd == "contact" and len(parts) == 3:
            cx, cy = self.set_contact(0, int(parts[1]), int(parts[2]))
            return f"ok 0 {cx} {cy}"
        elif cmd == "contact" and len(parts) >= 4:
            cid = int(parts[1])
            cx, cy = self.set_contact(cid, int(parts[2]), int(parts[3]))
            return f"ok {cid} {cx} {cy}"
        elif cmd == "send":
            return await self.flush()
        elif cmd == "release" and len(parts) == 1:
            return await self.release_all()
        elif cmd == "release" and len(parts) >= 2:
            await self.release(int(parts[1]))
            return "ok"
        elif cmd == "status":
            return await self.status()
        elif cmd == "ping":
            return "pong"
        else:
            return f"err unknown or bad args: {line}"


# ---------------------------------------------------------------------------
# TCP control server (same protocol as the C# version)
# ---------------------------------------------------------------------------

class ControlServer:
    def __init__(self, digitizer: Digitizer, port: int):
        self._digitizer = digitizer
        self._port = port

    async def start(self) -> None:
        server = await asyncio.start_server(self._handle_client, "127.0.0.1", self._port)
        log.info("[ctl] control endpoint on 127.0.0.1:%d  (contact / release)", self._port)
        asyncio.ensure_future(server.serve_forever())

    async def _handle_client(self, reader: asyncio.StreamReader,
                             writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        log.info("[ctl] client connected %s", peer)
        try:
            while True:
                data = await reader.readline()
                if not data:
                    break
                line = data.decode("ascii", errors="replace").strip()
                try:
                    reply = await self._digitizer.execute(line)
                except Exception as exc:
                    reply = f"err {exc}"
                writer.write((reply + "\n").encode())
                await writer.drain()
        except (ConnectionResetError, BrokenPipeError):
            pass
        finally:
            log.info("[ctl] client disconnected %s", peer)
            writer.close()


# ---------------------------------------------------------------------------
# BlueZ adapter helpers
# ---------------------------------------------------------------------------

def find_adapter(bus: dbus.SystemBus) -> str | None:
    om = dbus.Interface(bus.get_object(BLUEZ_BUS, "/"), DBUS_OM_IFACE)
    for path, ifaces in om.GetManagedObjects().items():
        if GATT_MANAGER_IFACE in ifaces:
            return path
    return None


def set_adapter_powered(bus: dbus.SystemBus, adapter_path: str) -> None:
    props = dbus.Interface(
        bus.get_object(BLUEZ_BUS, adapter_path), DBUS_PROP_IFACE)
    if not props.Get(ADAPTER_IFACE, "Powered"):
        props.Set(ADAPTER_IFACE, "Powered", dbus.Boolean(True))
        log.info("adapter powered on")


def set_le_only(hci: str = "hci0") -> bool:
    """Disable BR/EDR so iOS connects over BLE, not classic Bluetooth.
    Requires a power cycle — btmgmt rejects bredr changes while powered."""
    import subprocess
    def mgmt(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["btmgmt", "--index", hci, *args],
                              capture_output=True, text=True)

    r = mgmt("bredr", "off")
    if r.returncode != 0:
        mgmt("power", "off")
        r = mgmt("bredr", "off")
        mgmt("power", "on")
    if r.returncode == 0:
        log.info("adapter set to LE-only (BR/EDR disabled)")
        return True
    log.warning("failed to disable BR/EDR: %s", r.stderr.strip())
    return False


def restore_bredr(hci: str = "hci0") -> None:
    import subprocess
    def mgmt(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["btmgmt", "--index", hci, *args],
                              capture_output=True, text=True)

    mgmt("power", "off")
    mgmt("bredr", "on")
    mgmt("power", "on")
    log.info("adapter BR/EDR restored")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def build_gatt_app(bus: dbus.SystemBus) -> tuple[Application, Digitizer]:
    app = Application(bus)

    # HID service 0x1812
    hid_svc = Service(bus, 0, "00001812-0000-1000-8000-00805f9b34fb", primary=True)

    hid_info = HidInfoCharacteristic(bus, 0, hid_svc)
    hid_svc.add_characteristic(hid_info)

    report_map = ReportMapCharacteristic(bus, 1, hid_svc)
    hid_svc.add_characteristic(report_map)

    ctrl_point = ControlPointCharacteristic(bus, 2, hid_svc)
    hid_svc.add_characteristic(ctrl_point)

    proto_mode = ProtocolModeCharacteristic(bus, 3, hid_svc)
    hid_svc.add_characteristic(proto_mode)

    input_report = InputReportCharacteristic(bus, 4, hid_svc)
    ref_desc = ReportReferenceDescriptor(bus, 0, input_report, INPUT_REPORT_ID, 0x01)
    input_report.add_descriptor(ref_desc)
    hid_svc.add_characteristic(input_report)

    feature_report = FeatureReportCharacteristic(bus, 5, hid_svc)
    feat_ref_desc = ReportReferenceDescriptor(bus, 0, feature_report, FEATURE_REPORT_ID, 0x03)
    feature_report.add_descriptor(feat_ref_desc)
    hid_svc.add_characteristic(feature_report)

    app.add_service(hid_svc)

    # Device Information Service 0x180A
    dis_svc = Service(bus, 1, "0000180a-0000-1000-8000-00805f9b34fb", primary=True)
    pnp_id = PnPIdCharacteristic(bus, 0, dis_svc)
    dis_svc.add_characteristic(pnp_id)
    mfr_name = ManufacturerNameCharacteristic(bus, 1, dis_svc)
    dis_svc.add_characteristic(mfr_name)
    app.add_service(dis_svc)

    # Battery service 0x180F
    bat_svc = Service(bus, 2, "0000180f-0000-1000-8000-00805f9b34fb", primary=True)
    bat_level = BatteryLevelCharacteristic(bus, 0, bat_svc)
    bat_svc.add_characteristic(bat_level)
    app.add_service(bat_svc)

    digitizer = Digitizer(input_report)
    return app, digitizer


def register_callbacks(bus: dbus.SystemBus, adapter_path: str,
                       app: Application, adv: HidAdvertisement) -> None:
    gatt_mgr = dbus.Interface(
        bus.get_object(BLUEZ_BUS, adapter_path), GATT_MANAGER_IFACE)
    adv_mgr = dbus.Interface(
        bus.get_object(BLUEZ_BUS, adapter_path), LE_ADV_MANAGER_IFACE)

    def on_gatt_registered():
        log.info("GATT application registered")

    def on_gatt_error(error):
        log.error("GATT registration failed: %s", error)
        sys.exit(1)

    gatt_mgr.RegisterApplication(
        app.path, {},
        reply_handler=on_gatt_registered,
        error_handler=on_gatt_error,
    )

    def on_adv_registered():
        log.info("advertising HID touchscreen")

    def on_adv_error(error):
        log.error("advertisement registration failed: %s "
                  "(is bluetoothd running with --experimental?)", error)
        sys.exit(1)

    adv_mgr.RegisterAdvertisement(
        adv.path, {},
        reply_handler=on_adv_registered,
        error_handler=on_adv_error,
    )


async def run_asyncio_bridge(digitizer: Digitizer, port: int) -> None:
    """Run the TCP control server on the asyncio loop."""
    ctl = ControlServer(digitizer, port)
    await ctl.start()
    stop = asyncio.get_event_loop().create_future()
    for sig in (signal.SIGINT, signal.SIGTERM):
        asyncio.get_event_loop().add_signal_handler(sig, stop.set_result, None)
    await stop


def main() -> None:
    parser = argparse.ArgumentParser(description="Digitizer — BLE HID touchscreen digitizer")
    parser.add_argument("-p", "--port", type=int, default=8760, help="TCP control port (default 8760)")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    parser.add_argument("--adapter", default=None, help="BlueZ adapter path (auto-detected)")
    parser.add_argument("--probe", action="store_true", help="setup only, exit without control loop")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s.%(msecs)03d  %(message)s",
        datefmt="%H:%M:%S",
    )

    log.info("Digitizer — Python/BlueZ BLE HID touchscreen digitizer")

    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    bus = dbus.SystemBus()

    adapter_path = args.adapter or find_adapter(bus)
    if adapter_path is None:
        log.error("no BlueZ adapter found with GATT manager support — "
                  "is bluetoothd running with --experimental?")
        sys.exit(2)
    log.info("using adapter %s", adapter_path)

    set_adapter_powered(bus, adapter_path)
    hci_name = adapter_path.rsplit("/", 1)[-1]
    le_only = set_le_only(hci_name)

    # Register pairing agent
    agent = PairingAgent(bus)
    agent_mgr = dbus.Interface(
        bus.get_object(BLUEZ_BUS, "/org/bluez"), AGENT_MANAGER_IFACE)
    agent_mgr.RegisterAgent(agent.path, PairingAgent.CAPABILITY)
    agent_mgr.RequestDefaultAgent(agent.path)
    log.info("pairing agent registered (%s)", PairingAgent.CAPABILITY)

    app, digitizer = build_gatt_app(bus)
    adv = HidAdvertisement(bus, 0)

    register_callbacks(bus, adapter_path, app, adv)

    if args.probe:
        log.info("[PROBE RESULT] HID_0x1812_PUBLISHED")
        log.info("PROBE mode: setup succeeded, exiting.")
        return

    port = int(os.environ.get("DIGITIZER_PORT", str(args.port)))

    # Run GLib main loop in a thread, asyncio TCP server on the main thread
    import threading

    loop = GLib.MainLoop()
    glib_thread = threading.Thread(target=loop.run, daemon=True)
    glib_thread.start()
    log.info("GLib main loop started (D-Bus events)")

    log.info("[PROBE RESULT] HID_0x1812_PUBLISHED")

    try:
        asyncio.run(run_asyncio_bridge(digitizer, port))
    except KeyboardInterrupt:
        pass
    finally:
        loop.quit()
        if le_only:
            restore_bredr(hci_name)
        log.info("stopped.")


if __name__ == "__main__":
    main()
