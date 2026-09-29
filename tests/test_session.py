import queue
import struct
import threading
import time
import unittest

from canable import (Device, DeviceInfo, Feature, FirmwareError, Frame, ProtocolError,
                       QueueOverflow, ReceiveTimeout, SendUncertain, StateError, UnsupportedError)
from canable.device import _probe


FEATURES = Feature(0xE53B)
CAP = struct.pack("<10I", int(FEATURES), 160_000_000, 1, 256, 1, 128, 128, 1, 512, 1)
FD_CAP = CAP + struct.pack("<8I", 1, 32, 1, 16, 16, 1, 32, 1)


class FakeTransport:
    interface = 0
    path = "fake://CAN0"
    serial = "TEST"
    product = "Candlelight 2.5"

    def __init__(self):
        self.calls = []
        self.writes = []
        self.incoming = queue.Queue()
        self.closed = False
        self.feedback = 2
        self.failed_request = None
        self.write_error = None
        self.active_reads = 0

    def control(self, request, value=0, data=None, length=0, **kwargs):
        self.calls.append((request, value, data, length))
        if request == 22:
            return bytes([self.feedback])
        self.feedback = ord("=") if request == self.failed_request else 2
        if data is not None:
            return b""
        return {4: CAP, 5: struct.pack("<4BII", 1, 2, 5, 0, 0x260803, 0x200),
                11: FD_CAP, 14: bytes(12), 20: struct.pack("<H25s25sI", 0x468, b"STM32G431", b"Multiboard", 0),
                25: b"\2\0", 26: b"stored"}.get(request, bytes(length))

    def read(self):
        self.active_reads += 1
        try:
            value = self.incoming.get()
            if isinstance(value, Exception):
                raise value
            return value
        finally:
            self.active_reads -= 1

    def write(self, data):
        if self.write_error:
            raise self.write_error
        self.writes.append(data)

    def abort_read(self):
        self.incoming.put(b"")

    def close(self):
        if self.active_reads:
            raise AssertionError("freed transport while read was outstanding")
        self.closed = True


def fixture(queue_size=4096):
    transport = FakeTransport()
    info = _probe(transport)
    transport.calls.clear()
    device = Device({0: transport}, {0: info})
    return device, device.channel(queue_size=queue_size), transport


def wait_until(predicate):
    deadline = time.monotonic() + 1
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.001)
    if not predicate():
        raise AssertionError("background result not delivered")


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.device, self.channel, self.transport = fixture()

    def tearDown(self):
        self.transport.failed_request = None
        self.device.close()

    def start(self, **kwargs):
        return self.channel.configure(**kwargs).start()

    def test_probe_is_read_only(self):
        _probe(self.transport)
        self.assertEqual([c[0] for c in self.transport.calls], [4, 5])
        self.assertTrue(all(c[2] is None for c in self.transport.calls))

    def test_configure_only_uses_internal_loopback_then_stops(self):
        self.channel.configure()
        modes = [struct.unpack("<II", c[2]) for c in self.transport.calls if c[0] == 2]
        self.assertEqual(modes, [(0, 0x4010), (1, 0x4013), (0, 0x4010)])
        self.assertFalse(self.channel.running)
        self.assertEqual(self.channel.nominal_timing.bitrate, 1_000_000)

    def test_reconfigure_fd_to_classic_clears_stale_timing(self):
        self.channel.configure(data_bitrate=5_000_000)
        self.transport.calls.clear()
        self.channel.configure()
        # The last reset occurs after internal loopback, and only nominal timing
        # is set afterwards. This reproduces the firmware's closed-reset caveat.
        calls = self.transport.calls
        reset = max(i for i, c in enumerate(calls) if c[0] == 2 and struct.unpack("<II", c[2])[0] == 0)
        self.assertEqual([c[0] for c in calls[reset + 2:]], [1, 22])
        self.assertFalse(self.channel._fd)

    def test_every_control_has_feedback(self):
        self.start(data_bitrate=5_000_000)
        requests = [c[0] for c in self.transport.calls]
        self.assertTrue(all(requests[i + 1] == 22 for i in range(0, len(requests), 2)))

    def test_firmware_error_even_after_successful_usb(self):
        self.transport.failed_request = 1
        with self.assertRaises(FirmwareError) as caught:
            self.channel.configure()
        self.assertEqual(caught.exception.feedback, ord("="))
        self.assertFalse(self.channel.running)

    def test_send_wait_is_distinct_from_usb_write(self):
        self.start()
        receipt = self.channel.send(Frame(0x123, b"a"))
        self.assertIsNotNone(receipt.submitted_ns)
        with self.assertRaises(ReceiveTimeout):
            receipt.wait(0)
        self.transport.incoming.put(struct.pack("<BBBI", 7, 11, receipt.marker, 42))
        self.assertEqual(receipt.wait(1).device_timestamp_us, 42)
        self.assertEqual(self.channel.receive(1).kind, "tx_echo")

    def test_wait_timeout_keeps_marker_reserved(self):
        self.start()
        receipt = self.channel.send(Frame(1))
        with self.assertRaises(ReceiveTimeout):
            receipt.wait(0.001)
        self.assertIn(receipt.marker, self.channel._pending)

    def test_pending_limit_no_write_on_rejection(self):
        self.start()
        self.channel.send_many([Frame(1)] * 63)
        with self.assertRaises(QueueOverflow):
            self.channel.send(Frame(2))
        self.assertEqual(len(self.transport.writes), 1)

    def test_invalid_fd_not_sent(self):
        self.start()
        with self.assertRaises(StateError):
            self.channel.send(Frame(1, bytes(12), fd=True))
        self.assertFalse(self.transport.writes)

    def test_brs_cannot_be_silently_removed(self):
        self.start(data_bitrate=1_000_000)
        with self.assertRaises(ValueError):
            self.channel.send(Frame(1, b"", fd=True, brs=True))
        self.assertFalse(self.transport.writes)

    def test_lower_data_bitrate_rejected(self):
        with self.assertRaises(ValueError):
            self.channel.configure(bitrate=1_000_000, data_bitrate=500_000)
        self.assertFalse(any(c[2] is not None for c in self.transport.calls))

    def test_listen_only_cannot_transmit(self):
        self.channel.configure().start(mode="listen_only")
        with self.assertRaises(StateError):
            self.channel.send(Frame(1))

    def test_send_uncertain_never_retries(self):
        self.start()
        self.transport.write_error = OSError("unplugged during write")
        with self.assertRaises(SendUncertain):
            self.channel.send(Frame(1))
        with self.assertRaises(SendUncertain):
            self.channel.send(Frame(1))
        self.assertFalse(self.transport.writes)

    def test_receive_disconnect_surfaces_and_fails_receipts(self):
        self.start()
        receipt = self.channel.send(Frame(1))
        self.transport.incoming.put(OSError("disconnected"))
        with self.assertRaises(SendUncertain):
            receipt.wait(1)
        with self.assertRaises(OSError):
            self.channel.receive(0)

    def test_firmware_timeout_preserves_error_event(self):
        self.start()
        receipt = self.channel.send(Frame(1))
        data = bytes([0, 0, 0, 0, 0, 16, 0, 0])
        self.transport.incoming.put(struct.pack("<BBI8sI", 18, 13, 0, data, 0))
        with self.assertRaises(SendUncertain):
            receipt.wait(1)
        self.assertEqual(self.channel.receive(1).app_flags, 16)

    def test_no_echo_has_no_completion_claim(self):
        self.start()
        receipt = self.channel.send(Frame(1), echo=False)
        self.assertEqual(receipt.marker, 0)
        with self.assertRaises(UnsupportedError):
            receipt.wait()

    def test_host_overflow_visible(self):
        device, channel, transport = fixture(queue_size=1)
        try:
            channel.configure().start()
            transport.incoming.put(bytes.fromhex("030f01"))
            transport.incoming.put(bytes.fromhex("030f02"))
            wait_until(lambda: channel._fatal is not None)
            with self.assertRaises(QueueOverflow):
                channel.receive(0)
            self.assertEqual(channel.dropped_events, 1)
        finally:
            device.close()

    def test_close_joins_before_free(self):
        self.start()
        wait_until(lambda: self.transport.active_reads == 1)
        self.device.close()
        self.assertTrue(self.transport.closed)
        self.assertFalse(self.channel._thread.is_alive())
        self.device.close()

    def test_stop_requires_reconfigure(self):
        self.start()
        self.channel.stop()
        with self.assertRaises(StateError):
            self.channel.start()

    def test_pending_stop_requires_reopen(self):
        self.start()
        receipt = self.channel.send(Frame(1))
        self.channel.stop()
        with self.assertRaises(SendUncertain):
            receipt.wait(0)
        with self.assertRaises(StateError):
            self.channel.configure()

    def test_filter_layout(self):
        self.channel.configure()
        self.channel.add_filter(0x123, 0x7FF)
        raw = next(c[2] for c in self.transport.calls if c[0] == 21)
        self.assertEqual(raw, bytes.fromhex("0123010000ff0700000000000000000000"))

    def test_maintenance_not_implicit(self):
        self.start()
        with self.assertRaises(StateError):
            self.device.write_user_data(b"test")
        self.assertFalse(any(c[0] in (24, 27) for c in self.transport.calls))

    def test_unsupported_termination(self):
        with self.assertRaises(UnsupportedError):
            self.channel.set_termination(True)
        self.assertFalse(self.transport.calls)


if __name__ == "__main__":
    unittest.main()
