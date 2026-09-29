import random
import struct
import unittest

from canable import Frame, ProtocolError
from canable.models import TimingLimits
from canable.protocol import choose_timing, decode_transfer, encode_batch, encode_frame


class ProtocolTests(unittest.TestCase):
    def test_classic_golden(self):
        self.assertEqual(encode_frame(Frame(0x123, b"\x01\x02"), 9), bytes.fromhex("0a0a0023010000090102"))

    def test_fd_extended_golden(self):
        raw = encode_frame(Frame(0x1ABCDE, bytes(range(12)), extended=True, fd=True, brs=True), 255)
        self.assertEqual(raw[:8], bytes.fromhex("140a06debc1a80ff"))
        self.assertEqual(raw[8:], bytes(range(12)))

    def test_rtr_golden(self):
        self.assertEqual(encode_frame(Frame(0x7FF, remote=True, remote_dlc=8)), bytes.fromhex("090a00ff0700400008"))

    def test_frame_constraints(self):
        invalid = [dict(arbitration_id=-1), dict(arbitration_id=0x800), dict(arbitration_id=0x20000000, extended=True),
                   dict(arbitration_id=1, data=b"x" * 9), dict(arbitration_id=1, data=b"x" * 9, fd=True),
                   dict(arbitration_id=1, brs=True), dict(arbitration_id=1, remote=True, fd=True),
                   dict(arbitration_id=1, remote=True, data=b"x"), dict(arbitration_id=1, remote=True, remote_dlc=9)]
        for kwargs in invalid:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                Frame(**kwargs)
        with self.assertRaises(ValueError):
            encode_frame(Frame(1, fd=True, esi=True))

    def test_immutable_payload(self):
        data = bytearray(b"a")
        frame = Frame(1, data)
        data[0] = 2
        self.assertEqual(frame.data, b"a")

    def test_fd_max_batch_boundary(self):
        frame = Frame(1, bytes(64), fd=True)
        self.assertEqual(len(encode_batch([frame] * 28, [0] * 28)), 2018)
        with self.assertRaises(ValueError):
            encode_batch([frame] * 29, [0] * 29)
        for frames, markers in (([], []), ([Frame(1)] * 65, [0] * 65), ([Frame(1)], [])):
            with self.assertRaises(ValueError):
                encode_batch(frames, markers)

    def test_mixed_batch_respects_firmware_fixed_64_byte_copy(self):
        frames = [Frame(1, bytes(64), fd=True)] * 28 + [Frame(2)]
        # 2026 wire bytes fit, but firmware's last 64-byte copy would not.
        with self.assertRaises(ValueError):
            encode_batch(frames, [0] * len(frames))

    def test_full_64_slot_pool_is_not_submitted(self):
        with self.assertRaises(ValueError):
            encode_batch([Frame(1)] * 64, [0] * 64)

    def test_rx_with_timestamp(self):
        event = decode_transfer(bytes.fromhex("0d0c002301000078563412aabb"), 0, 123)[0]
        self.assertEqual(event.frame.data, b"\xaa\xbb")
        self.assertEqual(event.device_timestamp_us, 0x12345678)
        self.assertEqual(event.host_timestamp_ns, 123)

    def test_rx_no_timestamp(self):
        event = decode_transfer(bytes.fromhex("080c000100000055"), 1, 456, False)[0]
        self.assertEqual(event.frame, Frame(1, b"\x55"))
        self.assertIsNone(event.device_timestamp_us)

    def test_blob_mixed_events(self):
        blob = bytes.fromhex("0311070b01ffffffff030f32040e4f4b")
        events = decode_transfer(blob, 0, 20)
        self.assertEqual([e.kind for e in events], ["tx_echo", "bus_load", "debug"])
        self.assertEqual(events[0].device_timestamp_us, 0xFFFFFFFF)
        self.assertEqual(events[1].bus_load, 50)
        self.assertEqual(events[2].text, "OK")

    def test_error_event(self):
        raw = struct.pack("<BBI8sI", 18, 13, 0x40, bytes([0, 0, 0, 0, 0, 0x10, 128, 4]), 12)
        event = decode_transfer(raw, 1, 100)[0]
        self.assertEqual(event.error_id, 0x40)
        self.assertEqual(event.app_flags, 0x10)
        self.assertEqual(event.error_data[6:], bytes([128, 4]))

    def test_reject_truncation_and_trailing_bytes(self):
        valid = bytes.fromhex("070b0101000000")
        for length in range(len(valid)):
            with self.subTest(length=length), self.assertRaises(ProtocolError):
                decode_transfer(valid[:length], 0, 0)
        malformed = [valid + b"\0", b"\0\x11", b"\1\x11\2\x11", b"\2\x11" + valid,
                     b"\3\x0f\xff", bytes.fromhex("070b0000000000")]
        for data in malformed:
            with self.subTest(data=data), self.assertRaises(ProtocolError):
                decode_transfer(data, 0, 0)

    def test_unknown_retained(self):
        event = decode_transfer(b"\3\x77\x99", 0, 0)[0]
        self.assertEqual(event.kind, "unknown")
        self.assertEqual(event.raw, b"\3\x77\x99")

    def test_random_malformed_input_never_leaks_parser_errors(self):
        rng = random.Random(250803)
        for _ in range(2000):
            data = rng.randbytes(rng.randrange(0, 2200))
            try:
                decode_transfer(data, 0, 0)
            except ProtocolError:
                pass  # IndexError/struct.error/ValueError must NOT escape.

    def test_timing_for_g431_and_g0(self):
        limits = TimingLimits(1, 256, 1, 128, 128, 1, 512, 1)
        for clock in (160_000_000, 60_000_000):
            timing = choose_timing(clock, limits, 1_000_000)
            self.assertEqual(timing.bitrate, 1_000_000)
            self.assertEqual(timing.sample_point, 0.75)
        with self.assertRaises(ValueError):
            choose_timing(160_000_000, limits, 1_234_567)
        with self.assertRaises(ProtocolError):
            choose_timing(0, limits, 1_000_000)


if __name__ == "__main__":
    unittest.main()
