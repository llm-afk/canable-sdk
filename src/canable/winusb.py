"""ctypes WinUSB transport. No libusb DLL, driver replacement or protocol policy.

Control/OUT transfers have timeouts; IN is an overlapped pending read cancelled
only at close (application receive timeouts do not cancel USB reads).
Handles/buffers stay alive until the reader has exited. Windows ABI types have
explicit widths (not C long, whose width differs on other platforms).
"""
from __future__ import annotations

import ctypes as C
import os
import threading
import uuid

from .errors import ProtocolError, TransportError, UnsupportedError

U8, U16, U32, PTR = C.c_uint8, C.c_uint16, C.c_uint32, C.c_void_p
BOOL = C.c_int32
CANDLE_GUID = "c15b4308-04d3-11e6-b3ea-6057189e6443"
DFU_GUID = "c25b4308-04d3-11e6-b3ea-6057189e6443"
INVALID = C.c_void_p(-1).value


class GUID(C.Structure):
    _fields_ = [("data", U8 * 16)]


class InterfaceData(C.Structure):
    _fields_ = [("cbSize", U32), ("guid", GUID), ("flags", U32), ("reserved", C.c_size_t)]


class Setup(C.Structure):
    _pack_ = 1
    _fields_ = [("type", U8), ("request", U8), ("value", U16), ("index", U16), ("length", U16)]


class InterfaceDescriptor(C.Structure):
    _pack_ = 1
    _fields_ = [(name, U8) for name in (
        "length", "type", "number", "alternate", "endpoints", "klass", "subclass", "protocol", "string")]


class PipeInfo(C.Structure):
    _fields_ = [("type", U32), ("id", U8), ("max_packet", U16), ("interval", U8)]


class Overlapped(C.Structure):
    _fields_ = [("internal", C.c_size_t), ("internal_high", C.c_size_t),
                ("offset", U32), ("offset_high", U32), ("event", PTR)]


def _bind(dll, name, result, *args):
    fn = getattr(dll, name)
    fn.restype, fn.argtypes = result, list(args)
    return fn


class _API:
    def __init__(self):
        if os.name != "nt":
            raise UnsupportedError("native WinUSB transport requires Windows")
        self.k = C.WinDLL("kernel32", use_last_error=True)
        self.u = C.WinDLL("winusb", use_last_error=True)
        self.s = C.WinDLL("setupapi", use_last_error=True)
        _bind(self.k, "CreateFileW", PTR, C.c_wchar_p, U32, U32, PTR, U32, U32, PTR)
        _bind(self.k, "CloseHandle", BOOL, PTR)
        _bind(self.k, "CreateEventW", PTR, PTR, BOOL, BOOL, C.c_wchar_p)
        _bind(self.k, "ResetEvent", BOOL, PTR)
        _bind(self.u, "WinUsb_Initialize", BOOL, PTR, C.POINTER(PTR))
        _bind(self.u, "WinUsb_Free", BOOL, PTR)
        _bind(self.u, "WinUsb_QueryInterfaceSettings", BOOL, PTR, U8, C.POINTER(InterfaceDescriptor))
        _bind(self.u, "WinUsb_QueryPipe", BOOL, PTR, U8, U8, C.POINTER(PipeInfo))
        _bind(self.u, "WinUsb_SetPipePolicy", BOOL, PTR, U8, U32, U32, PTR)
        _bind(self.u, "WinUsb_GetDescriptor", BOOL, PTR, U8, U8, U16, PTR, U32, C.POINTER(U32))
        _bind(self.u, "WinUsb_ControlTransfer", BOOL, PTR, Setup, PTR, U32, C.POINTER(U32), PTR)
        for name in ("WinUsb_ReadPipe", "WinUsb_WritePipe"):
            _bind(self.u, name, BOOL, PTR, U8, PTR, U32, C.POINTER(U32), PTR)
        _bind(self.u, "WinUsb_AbortPipe", BOOL, PTR, U8)
        _bind(self.u, "WinUsb_GetOverlappedResult", BOOL, PTR, C.POINTER(Overlapped), C.POINTER(U32), BOOL)
        _bind(self.s, "SetupDiGetClassDevsW", PTR, C.POINTER(GUID), C.c_wchar_p, PTR, U32)
        _bind(self.s, "SetupDiEnumDeviceInterfaces", BOOL, PTR, PTR, C.POINTER(GUID), U32, C.POINTER(InterfaceData))
        _bind(self.s, "SetupDiGetDeviceInterfaceDetailW", BOOL, PTR, C.POINTER(InterfaceData), PTR, U32, C.POINTER(U32), PTR)
        _bind(self.s, "SetupDiDestroyDeviceInfoList", BOOL, PTR)


def _check(ok, operation):
    if not ok:
        raise TransportError(operation, C.get_last_error())


def _registered_interface_guids(include_dfu: bool) -> set[str]:
    """Discover INF/Zadig-assigned GUIDs without changing driver registration.

    Registry may contain unplugged instances; SetupAPI's PRESENT flag later
    excludes those. Only this VID/PID bound to WinUSB is considered.
    """
    import winreg

    found = set()

    def children(key):
        index = 0
        while True:
            try:
                yield winreg.EnumKey(key, index)
            except OSError as exc:
                if exc.winerror == 259:
                    return
                raise
            index += 1

    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Enum\USB") as usb:
        for hardware in children(usb):
            name = hardware.lower()
            if not (name == "vid_1d50&pid_606f" or name.startswith("vid_1d50&pid_606f&mi_")):
                continue
            if not include_dfu and name.endswith("&mi_01"):
                continue
            with winreg.OpenKey(usb, hardware) as model:
                for instance in children(model):
                    try:
                        with winreg.OpenKey(model, instance) as device:
                            service = winreg.QueryValueEx(device, "Service")[0]
                            if service.lower() != "winusb":
                                continue
                            with winreg.OpenKey(device, "Device Parameters") as parameters:
                                values = winreg.QueryValueEx(parameters, "DeviceInterfaceGUIDs")[0]
                                if isinstance(values, str):
                                    values = [values]
                                for value in values:
                                    try:
                                        found.add(str(uuid.UUID(value.strip("{}"))))
                                    except ValueError:
                                        continue
                    except FileNotFoundError:
                        continue  # Device or optional value disappeared.
    return found


def enumerate_paths(include_dfu: bool = False) -> list[str]:
    api, paths = _API(), []
    guids = {CANDLE_GUID, DFU_GUID} if include_dfu else {CANDLE_GUID}
    guids.update(_registered_interface_guids(include_dfu))
    for guid_text in sorted(guids):
        guid = GUID((U8 * 16).from_buffer_copy(uuid.UUID(guid_text).bytes_le))
        handle = api.s.SetupDiGetClassDevsW(C.byref(guid), None, None, 0x12)
        if handle == INVALID:
            raise TransportError("SetupDiGetClassDevsW", C.get_last_error())
        try:
            index = 0
            while True:
                item = InterfaceData()
                item.cbSize = C.sizeof(item)
                if not api.s.SetupDiEnumDeviceInterfaces(handle, None, C.byref(guid), index, C.byref(item)):
                    err = C.get_last_error()
                    if err == 259:  # ERROR_NO_MORE_ITEMS
                        break
                    raise TransportError("SetupDiEnumDeviceInterfaces", err)
                size = U32()
                ok = api.s.SetupDiGetDeviceInterfaceDetailW(handle, C.byref(item), None, 0, C.byref(size), None)
                if ok or C.get_last_error() != 122 or size.value < 6:
                    raise ProtocolError("unexpected SetupAPI detail size result")
                detail = C.create_string_buffer(size.value)
                # SP_DEVICE_INTERFACE_DETAIL_DATA_W has path at offset 4;
                # cbSize includes ABI padding, 8 on x64, 6 on x86.
                U32.from_buffer(detail).value = 8 if C.sizeof(PTR) == 8 else 6
                _check(api.s.SetupDiGetDeviceInterfaceDetailW(handle, C.byref(item), detail,
                        size, C.byref(size), None), "SetupDiGetDeviceInterfaceDetailW")
                path = C.wstring_at(C.addressof(detail) + 4)
                if "vid_1d50&pid_606f" in path.lower():
                    paths.append(path)
                index += 1
        finally:
            api.s.SetupDiDestroyDeviceInfoList(handle)
    return sorted(set(paths))


class WinUSBTransport:
    """Exclusive interface handle. Caller serializes controls and writes.

    read() may run concurrently with control()/write(). close() must run AFTER
    the read thread joins. abort_read() can be called from another thread.
    """
    def __init__(self, path: str):
        self.api = _API()
        self.path, self.file, self.usb = path, None, PTR()
        self.in_pipe, self.out_pipe = None, None
        self.out_packet_size = None
        self._last_out_zlp = None
        self._io_lock = threading.Lock()
        self._io_stopped = False
        self._read_event = None
        try:
            self.file = self.api.k.CreateFileW(path, 0xC0000000, 0, None, 3, 0x40000080, None)
            if self.file == INVALID:
                raise TransportError("CreateFileW (device busy, inaccessible or removed)", C.get_last_error())
            _check(self.api.u.WinUsb_Initialize(self.file, C.byref(self.usb)), "WinUsb_Initialize")
            self._timeout(0, 1000)
            desc = self._descriptor(1, 0, 18)
            if len(desc) != 18 or desc[8:12] != bytes.fromhex("501d6f60"):
                raise ProtocolError("not a Candlelight USB device")
            interface = InterfaceDescriptor()
            _check(self.api.u.WinUsb_QueryInterfaceSettings(self.usb, 0, C.byref(interface)), "WinUsb_QueryInterfaceSettings")
            self.interface = interface.number
            self.serial = self._string(desc[16])
            # Firmware uses manufacturer=1, product=2, serial=3. WinUSB may
            # substitute the interface string index for iProduct.
            self.product = self._string(2 if desc[14] == 1 and desc[16] == 3 else desc[15])
            for i in range(interface.endpoints):
                pipe = PipeInfo()
                _check(self.api.u.WinUsb_QueryPipe(self.usb, 0, i, C.byref(pipe)), "WinUsb_QueryPipe")
                if pipe.type != 2:
                    raise ProtocolError("expected bulk endpoints")
                if pipe.id & 0x80:
                    self.in_pipe = pipe.id
                    self._timeout(pipe.id, 0)
                else:
                    self.out_pipe = pipe.id
                    self.out_packet_size = pipe.max_packet
                    self._timeout(pipe.id, 500)
            if self.interface != 1 and (self.in_pipe is None or self.out_pipe is None):
                raise ProtocolError("missing CAN bulk endpoints")
            if self.in_pipe is not None:
                self._read_event = self.api.k.CreateEventW(None, True, False, None)
                _check(self._read_event, "CreateEventW")
        except BaseException:
            self.close()
            raise

    def _timeout(self, pipe: int, ms: int):
        timeout = U32(ms)
        _check(self.api.u.WinUsb_SetPipePolicy(self.usb, pipe, 3, 4, C.byref(timeout)), "WinUsb_SetPipePolicy")

    def _descriptor(self, kind: int, index: int, size: int, language: int = 0) -> bytes:
        buf, count = C.create_string_buffer(size), U32()
        _check(self.api.u.WinUsb_GetDescriptor(self.usb, kind, index, language, buf, size, C.byref(count)), "WinUsb_GetDescriptor")
        return buf.raw[:count.value]

    def _string(self, index: int) -> str:
        if not index:
            return ""
        raw = self._descriptor(3, index, 255, 0x409)
        if len(raw) < 2 or raw[1] != 3 or raw[0] > len(raw) or raw[0] % 2:
            raise ProtocolError("invalid USB string descriptor")
        return raw[2:raw[0]].decode("utf-16-le")

    def control(self, request: int, value: int = 0, data: bytes | None = None,
                length: int = 0, *, class_request: bool = False) -> bytes:
        incoming = data is None
        size = length if incoming else len(data)
        if not 0 <= size <= 4096:
            raise ValueError("control transfer outside 0..4096 bytes")
        buf, count = C.create_string_buffer(max(1, size)), U32()
        if not incoming and size:
            C.memmove(buf, data, size)
        setup = Setup((0xA1 if class_request else 0xC1) if incoming else
                      (0x21 if class_request else 0x41), request, value, self.interface, size)
        _check(self.api.u.WinUsb_ControlTransfer(self.usb, setup, buf, size, C.byref(count), None), "WinUsb_ControlTransfer")
        if not incoming and count.value != size:
            raise ProtocolError("short control write")
        return buf.raw[:count.value] if incoming else b""

    def read(self) -> bytes:
        buf, count = C.create_string_buffer(2048), U32()
        overlapped = Overlapped()
        overlapped.event = self._read_event
        with self._io_lock:
            if self._io_stopped:
                return b""
            _check(self.api.k.ResetEvent(self._read_event), "ResetEvent")
            ok = self.api.u.WinUsb_ReadPipe(self.usb, self.in_pipe, buf, len(buf), None, C.byref(overlapped))
            if not ok and C.get_last_error() != 997:  # ERROR_IO_PENDING
                raise TransportError("WinUsb_ReadPipe", C.get_last_error())
        # Keep both local objects alive through completion, including cancel.
        _check(self.api.u.WinUsb_GetOverlappedResult(self.usb, C.byref(overlapped), C.byref(count), True),
               "WinUsb_GetOverlappedResult")
        return buf.raw[:count.value]

    def write(self, data: bytes):
        if not 1 <= len(data) <= 2048:
            raise ValueError("bulk write outside 1..2048 bytes")
        # Firmware arms a 2048-byte OUT read. Shorter transfers ending exactly
        # on a USB packet boundary need ZLP. At exactly 2048 it has already
        # completed/rearmed; an extra ZLP would reparse stale firmware data.
        terminate = len(data) < 2048 and len(data) % self.out_packet_size == 0
        if terminate != self._last_out_zlp:
            policy = U8(terminate)
            _check(self.api.u.WinUsb_SetPipePolicy(self.usb, self.out_pipe, 1, 1, C.byref(policy)),
                   "WinUsb_SetPipePolicy(SHORT_PACKET_TERMINATE)")
            self._last_out_zlp = terminate
        buf, count = C.create_string_buffer(data, len(data)), U32()
        _check(self.api.u.WinUsb_WritePipe(self.usb, self.out_pipe, buf, len(data), C.byref(count), None), "WinUsb_WritePipe")
        if count.value != len(data):
            raise ProtocolError("short bulk write; send result uncertain")

    def abort_read(self):
        with self._io_lock:
            self._io_stopped = True
            if self.usb and self.in_pipe is not None:
                self.api.u.WinUsb_AbortPipe(self.usb, self.in_pipe)

    def close(self):
        if self.usb:
            self.api.u.WinUsb_Free(self.usb)
            self.usb = PTR()
        if self.file not in (None, INVALID):
            self.api.k.CloseHandle(self.file)
        self.file = None
        if self._read_event:
            self.api.k.CloseHandle(self._read_event)
            self._read_event = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
