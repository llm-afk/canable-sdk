import ctypes
import struct
import threading
import time
import unittest
from unittest.mock import patch

from canable import Device, FirmwareError, ProtocolError, StateError, TransportError, UnsupportedError
from canable.device import _probe, discover
from canable.winusb import InterfaceData, InterfaceDescriptor, Overlapped, PipeInfo, Setup
from test_session import FakeTransport, fixture


class ContextFake(FakeTransport):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class DeviceTests(unittest.TestCase):
    def test_windows_abi_layout(self):
        self.assertEqual(ctypes.sizeof(Setup), 8)
        self.assertEqual(ctypes.sizeof(InterfaceDescriptor), 9)
        self.assertEqual(ctypes.sizeof(PipeInfo), 12)
        self.assertEqual(PipeInfo.max_packet.offset, 6)
        is64 = ctypes.sizeof(ctypes.c_void_p) == 8
        self.assertEqual(ctypes.sizeof(InterfaceData), 32 if is64 else 28)
        self.assertEqual(ctypes.sizeof(Overlapped), 32 if is64 else 20)

    def test_discovery_retains_access_errors(self):
        with patch("canable.device.enumerate_paths", return_value=["busy"]), patch(
                "canable.device.WinUSBTransport", side_effect=TransportError("open", 5)):
            entries = discover()
        self.assertEqual(entries[0].path, "busy")
        self.assertIn("5", entries[0].error)

    def test_discovery_closes_on_probe_failure(self):
        transport = ContextFake()
        with patch("canable.device.enumerate_paths", return_value=["fake"]), patch(
                "canable.device.WinUSBTransport", return_value=transport), patch(
                "canable.device._probe", side_effect=ProtocolError("short version")):
            self.assertIsNotNone(discover()[0].error)
        self.assertTrue(transport.closed)

    def test_open_rejects_ambiguous_devices(self):
        a, b = FakeTransport(), FakeTransport()
        b.serial = "OTHER"
        with patch("canable.device.discover", return_value=[_probe(a), _probe(b)]):
            with self.assertRaises(StateError):
                Device.open()

    def test_open_rejects_old_firmware_before_mutation(self):
        from dataclasses import replace
        info = replace(_probe(FakeTransport()), firmware=0x260518)
        with patch("canable.device.discover", return_value=[info]), patch("canable.device.WinUSBTransport") as factory:
            with self.assertRaises(UnsupportedError):
                Device.open()
            factory.assert_not_called()

    def test_open_requires_all_channels(self):
        from dataclasses import replace
        info = replace(_probe(FakeTransport()), channel_count=2)
        with patch("canable.device.discover", return_value=[info]):
            with self.assertRaises(StateError):
                Device.open()

    def test_identity_change_releases_handle(self):
        transport = FakeTransport()
        info = _probe(transport)
        transport.serial = "NEW"
        with patch("canable.device.discover", return_value=[info]), patch(
                "canable.device.WinUSBTransport", return_value=transport):
            with self.assertRaises(StateError):
                Device.open(serial="TEST")
        self.assertTrue(transport.closed)

    def test_control_pairs_are_serialized(self):
        device, _, transport = fixture()
        original = transport.control

        def slow(*args, **kwargs):
            result = original(*args, **kwargs)
            time.sleep(0.001)
            return result

        transport.control = slow
        threads = [threading.Thread(target=device._request, args=(0, 7), kwargs={"data": bytes(4)}) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual([c[0] for c in transport.calls], [7, 22] * 8)
        device.close()

    def test_short_control_read_not_accepted(self):
        device, _, _ = fixture()
        with self.assertRaises(ProtocolError):
            device._request(0, 26, length=2000)
        device.close()

    def test_failed_control_preserves_firmware_feedback(self):
        device, _, transport = fixture()
        original = transport.control

        def failing(request, *args, **kwargs):
            if request != 22:
                transport.feedback = ord("4")
                raise TransportError("control", 31)
            return original(request, *args, **kwargs)

        transport.control = failing
        with self.assertRaises(FirmwareError) as caught:
            device._request(0, 1, data=bytes(20))
        self.assertEqual(caught.exception.feedback, ord("4"))
        device.close()

    def test_maintenance_checks_physical_channels(self):
        device, channel, transport = fixture()
        try:
            channel.configure()
            # Local channel stopped, but fake firmware reports active.
            with self.assertRaises(StateError):
                device.write_user_data(b"x")
            self.assertFalse(any(c[0] == 27 for c in transport.calls))
        finally:
            device.close()

    def test_user_storage_and_board_info(self):
        device, channel, transport = fixture()
        original = transport.control

        def stopped(request, *args, **kwargs):
            result = original(request, *args, **kwargs)
            return struct.pack("<III", 4, 0, 0) if request == 14 else result

        transport.control = stopped
        try:
            channel.configure()
            self.assertEqual(device.board_info()["mcu"], "STM32G431")
            self.assertTrue(device.boot_pin_enabled())
            self.assertEqual(device.read_user_data(), b"stored")
            device.write_user_data(b"hello")
            call = next(c for c in transport.calls if c[0] == 27)
            self.assertEqual(call[1:3], (0, b"hello"))
        finally:
            device.close()

    def test_multi_channel_control_routes_channel_and_transport(self):
        first, second = FakeTransport(), FakeTransport()
        second.interface = 2
        second.path = "fake://CAN1"
        device = Device({0: first, 1: second}, {0: _probe(first), 1: _probe(second)})
        first.calls.clear()
        second.calls.clear()
        try:
            channel = device.channel(1).configure()
            channel.set_bridge_filter(0, 0, 0x100, 0x700)
            self.assertFalse(first.calls)
            self.assertTrue(all(c[1] == 1 for c in second.calls))
            raw = next(c[2] for c in second.calls if c[0] == 21)
            self.assertEqual(struct.unpack("<BIIBB6x", raw), (11, 0x100, 0x700, 0, 0))
        finally:
            device.close()

    def test_dfu_closes_readers_before_detach(self):
        device, channel, transport = fixture()
        original = transport.control

        def stopped(request, *args, **kwargs):
            result = original(request, *args, **kwargs)
            return struct.pack("<III", 4, 0, 0) if request == 14 else result

        transport.control = stopped

        class Dfu:
            def control(self, request, **kwargs):
                if transport.active_reads:
                    raise AssertionError("read still pending during DFU detach")
                return bytes([0, 0, 0, 0, 1, 255]) if request == 3 else b""

            def close(self):
                pass

        device._dfu = Dfu()
        try:
            channel.configure()
            self.assertEqual(device.enter_dfu(), "replug_required")
            self.assertTrue(device._closed)
            self.assertTrue(transport.closed)
        finally:
            device.close()


if __name__ == "__main__":
    unittest.main()
