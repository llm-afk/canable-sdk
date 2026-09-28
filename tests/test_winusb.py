import ctypes as C
import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from canable25 import ProtocolError
from canable25.winusb import U8, U32, WinUSBTransport, _registered_interface_guids


class BulkAPIFake:
    def __init__(self, short=False):
        self.policies, self.writes, self.short = [], [], short

    def WinUsb_SetPipePolicy(self, handle, pipe, policy, size, value):
        self.policies.append((pipe, policy, size, C.cast(value, C.POINTER(U8)).contents.value))
        return True

    def WinUsb_WritePipe(self, handle, pipe, buffer, size, count, overlapped):
        self.writes.append(C.string_at(buffer, size))
        C.cast(count, C.POINTER(U32)).contents.value = size - 1 if self.short else size
        return True


def make_transport(short=False):
    transport = WinUSBTransport.__new__(WinUSBTransport)
    native = BulkAPIFake(short)
    transport.api = SimpleNamespace(u=native)
    transport.usb, transport.out_pipe, transport.out_packet_size = 1, 2, 64
    transport._last_out_zlp = None
    return transport, native


class WinUSBTests(unittest.TestCase):
    def test_zlp_for_packet_boundary_but_not_full_firmware_buffer(self):
        transport, native = make_transport()
        for size in (64, 128, 2048, 64):
            transport.write(bytes(size))
        self.assertEqual(native.policies, [(2, 1, 1, 1), (2, 1, 1, 0), (2, 1, 1, 1)])
        self.assertEqual([len(data) for data in native.writes], [64, 128, 2048, 64])

    def test_no_zlp_for_nonboundary_transfer(self):
        transport, native = make_transport()
        transport.write(bytes(72))
        self.assertEqual(native.policies, [(2, 1, 1, 0)])

    def test_partial_bulk_write_is_not_success(self):
        transport, _ = make_transport(short=True)
        with self.assertRaises(ProtocolError):
            transport.write(bytes(16))

    @unittest.skipUnless(os.name == "nt", "Windows registry API")
    def test_registry_guid_override_is_read_only_and_device_scoped(self):
        import winreg
        can, dfu, other = "VID_1D50&PID_606F&MI_00", "VID_1D50&PID_606F&MI_01", "VID_1234&PID_5678"
        root = r"SYSTEM\CurrentControlSet\Enum\USB"
        tree = {root: [can, dfu, other], root + "\\" + can: ["one", "two"], root + "\\" + dfu: ["one"]}
        guid = "{D62F2386-83BC-4AB7-9E4A-2856D8E8DA56}"
        values = {
            (root + "\\" + can + r"\one", "Service"): "WinUSB",
            (root + "\\" + can + r"\two", "Service"): "libusbK",
            (root + "\\" + can + r"\one\Device Parameters", "DeviceInterfaceGUIDs"): [guid, "invalid"],
            (root + "\\" + dfu + r"\one", "Service"): "WinUSB",
            (root + "\\" + dfu + r"\one\Device Parameters", "DeviceInterfaceGUIDs"): ["{c25b4308-04d3-11e6-b3ea-6057189e6443}"],
        }

        class Key:
            def __init__(self, path):
                self.path = path

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

        def open_key(parent, child):
            return Key(child if not isinstance(parent, Key) else parent.path + "\\" + child)

        def enum_key(key, index):
            children = tree.get(key.path, [])
            if index < len(children):
                return children[index]
            exc = OSError("no more items")
            exc.winerror = 259
            raise exc

        def query(key, name):
            return values[(key.path, name)], 7

        with patch.object(winreg, "OpenKey", side_effect=open_key), patch.object(winreg, "EnumKey", side_effect=enum_key), patch.object(winreg, "QueryValueEx", side_effect=query):
            self.assertEqual(_registered_interface_guids(False), {guid.strip("{}").lower()})
            self.assertEqual(len(_registered_interface_guids(True)), 2)
