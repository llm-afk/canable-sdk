"""Device ownership and firmware control transactions."""
from __future__ import annotations

from dataclasses import replace
import struct
import threading

from .errors import FirmwareError, ProtocolError, StateError, UnsupportedError
from .models import DeviceInfo, Feature
from .protocol import MIN_FIRMWARE
from .winusb import WinUSBTransport, enumerate_paths


def _exact(data: bytes, size: int) -> bytes:
    if len(data) != size:
        raise ProtocolError(f"expected {size} bytes, received {len(data)}")
    return data


def _probe(transport) -> DeviceInfo:
    interface = transport.interface
    info = DeviceInfo(transport.path, transport.serial, transport.product, interface,
                      None if interface == 1 else (0 if interface == 0 else interface - 1))
    if info.channel is None:
        return info
    cap = struct.unpack("<10I", _exact(transport.control(4, info.channel, length=40), 40))
    version = struct.unpack("<4BII", _exact(transport.control(5, info.channel, length=12), 12))
    return replace(info, firmware=version[4], hardware=version[5], channel_count=version[3] + 1,
                   features=Feature(cap[0]), can_clock_hz=cap[1])


def discover(*, include_dfu: bool = False) -> list[DeviceInfo]:
    """Read-only descriptors/capabilities/version; never reset/start a CAN bus.

    Busy/inaccessible interfaces remain visible with path and error, rather than
    being silently omitted. Serial comes from USB descriptor, not path parsing.
    """
    result = []
    for path in enumerate_paths(include_dfu):
        try:
            with WinUSBTransport(path) as transport:
                result.append(_probe(transport))
        except Exception as exc:
            result.append(DeviceInfo(path, "", "", -1, None, error=str(exc)))
    return result


class Device:
    """Own every Candlelight interface of one physical adapter.

    All firmware command+feedback pairs use one device-wide lock because the
    firmware stores last-error and protocol selection globally. Do not mix
    other software on this adapter. Each interface is opened exclusively.
    """
    def __init__(self, transports: dict[int, object], infos: dict[int, DeviceInfo], dfu=None):
        self._transports, self.infos, self._dfu = transports, infos, dfu
        self._lock = threading.RLock()
        self._channels = {}
        self._closed = False

    @classmethod
    def open(cls, *, serial: str | None = None) -> Device:
        entries = discover(include_dfu=True)
        candidates = [i for i in entries if i.channel is not None and not i.error]
        serials = {i.serial for i in candidates}
        if serial is None:
            if len(serials) != 1:
                raise StateError(f"expected exactly one accessible adapter, found {len(serials)}; select serial explicitly; discover() includes access errors")
            serial = serials.pop()
        selected = [i for i in candidates if i.serial == serial]
        if not selected or not serial:
            raise StateError(f"adapter {serial!r} not found or unavailable")
        expected = selected[0].channel_count
        if expected is None or {i.channel for i in selected} != set(range(expected)) or len(selected) != expected:
            raise StateError("cannot reserve all CAN interfaces; device may be busy or serial is ambiguous")
        for info in selected:
            if not info.features & Feature.ELMUE or (info.firmware or 0) < MIN_FIRMWARE:
                raise UnsupportedError(f"SDK requires Elmue Candlelight firmware >= 0x{MIN_FIRMWARE:06X}; no silent legacy fallback")
        transports, infos, dfu = {}, {}, None
        try:
            for info in selected:
                transport = WinUSBTransport(info.path)
                transports[info.channel] = transport
                current = _probe(transport)
                if current.serial != serial or current != info:
                    raise StateError("device identity changed during open; enumerate again")
                infos[info.channel] = current
            firmware_interfaces = [i for i in entries if i.interface == 1 and i.serial == serial and not i.error]
            if len(firmware_interfaces) == 1:
                dfu = WinUSBTransport(firmware_interfaces[0].path)
                if dfu.serial != serial or dfu.interface != 1:
                    raise StateError("DFU interface identity changed")
            return cls(transports, infos, dfu)
        except BaseException:
            for transport in transports.values():
                transport.close()
            if dfu:
                dfu.close()
            raise

    def _request(self, channel: int, request: int, *, value: int | None = None,
                 data: bytes | None = None, length: int = 0, variable: bool = False) -> bytes:
        with self._lock:
            if self._closed:
                raise StateError("device is closed")
            transport = self._transports[channel]
            actual_value = channel if value is None else value
            original = None
            try:
                result = transport.control(request, actual_value, data, length)
            except Exception as exc:
                original = exc
                result = b""
            # Check firmware completion even when USB itself reports success.
            try:
                feedback = _exact(transport.control(22, actual_value, length=1), 1)[0]
            except Exception:
                if original:
                    raise original
                raise
            if feedback != 2:
                raise FirmwareError(request, feedback) from original
            if original:
                raise original
            if data is None and not variable:
                _exact(result, length)
            return result

    def channel(self, index: int = 0, *, queue_size: int = 4096):
        from .channel import Channel
        with self._lock:
            if self._closed or index not in self._transports:
                raise StateError("device closed or invalid channel")
            if index not in self._channels:
                self._channels[index] = Channel(self, index, queue_size)
            return self._channels[index]

    def board_info(self) -> dict:
        """Requires configure() on at least one channel to enable Elmue protocol."""
        raw = self._request(0, 20, length=56)
        ident, mcu, board, flags = struct.unpack("<H25s25sI", raw)
        return dict(mcu_device_id=ident, mcu=mcu.split(b"\0")[0].decode("ascii"),
                    board=board.split(b"\0")[0].decode("ascii"), flags=flags)

    def _require_maintenance(self):
        if any(ch.running for ch in self._channels.values()):
            raise StateError("stop all SDK channels before maintenance")
        if not any(ch.prepared for ch in self._channels.values()):
            raise StateError("configure a channel first to initialize Elmue protocol")
        for index in self.infos:
            state = struct.unpack("<III", self._request(index, 14, length=12))[0]
            if state != 4:
                raise StateError(f"firmware channel {index} is not stopped; stop it before maintenance")

    def read_user_data(self) -> bytes:
        with self._lock:
            self._require_maintenance()
            return self._request(0, 26, value=0, length=2000, variable=True)

    def write_user_data(self, data: bytes):
        """Erase/write reserved segment 0; empty bytes erase it. No arbitrary flash writes."""
        if not isinstance(data, (bytes, bytearray, memoryview)) or len(data) > 2000:
            raise ValueError("user data must be bytes-like, <=2000 bytes")
        with self._lock:
            self._require_maintenance()
            self._request(0, 27, value=0, data=bytes(data))

    def boot_pin_enabled(self) -> bool:
        return bool(struct.unpack("<H", self._request(0, 25, value=1, length=2))[0] & 2)

    def disable_boot_pin(self):
        """Explicit persistent option-byte operation; not called by open/configure."""
        with self._lock:
            self._require_maintenance()
            self._request(0, 24, data=struct.pack("<HHII", 5, 1, 0, 0))

    def enter_dfu(self) -> str:
        """Request ROM DFU entry, not firmware programming. Device must be reopened.

        Return 'replug_required' or 'detach_requested'; neither proves flashing.
        """
        with self._lock:
            self._require_maintenance()
            if self._dfu is None:
                raise UnsupportedError("firmware-update interface unavailable")
        # Finish readers before the USB device disappears. No join under the
        # device lock; lifecycle methods are called by one owning thread.
        for channel in self._channels.values():
            channel.close()
        with self._lock:
            self._dfu.control(0, data=b"", class_request=True)
            raw = _exact(self._dfu.control(3, length=6, class_request=True), 6)
            if raw[4] == 10 or raw[0]:
                raise FirmwareError(0, raw[5])
            result = "replug_required" if raw[4] == 1 else "detach_requested"
        self.close()
        return result

    def close(self):
        # Never hold the device lock while joining readers.
        if self._closed:
            return
        failures = []
        for channel in self._channels.values():
            try:
                channel.close()
            except Exception as exc:
                failures.append(exc)
        if any(ch._thread is not None and ch._thread.is_alive() for ch in self._channels.values()):
            raise StateError("reader has not stopped; USB handles retained, retry close()")
        with self._lock:
            for transport in self._transports.values():
                transport.close()
            if self._dfu:
                self._dfu.close()
            self._closed = True
        if failures:
            raise failures[0]

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        try:
            self.close()
        except Exception:
            if exc is None:
                raise
