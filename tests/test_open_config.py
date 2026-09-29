import struct
import unittest
from unittest.mock import patch
from canable import open_can, CanFilter, Feature, UnsupportedError, FirmwareError
from test_session import fixture

class OpenConfigTests(unittest.TestCase):
    def test_configuration_precedes_start_on_wire(self):
        device, channel, transport = fixture()
        with patch("canable.connection.Device.open", return_value=device):
            with open_can(queue_size=17, filters=[CanFilter(0x123, 0x7FF),
                          CanFilter(0x123456, 0x1FFFFFFF, extended=True)],
                          bus_load_interval_ms=500, mode="listen_only") as connection:
                calls = transport.calls
                filters = [c for c in calls if c[0] == 21]
                self.assertEqual(len(filters), 3)
                self.assertEqual(filters[1][2], struct.pack("<BIIBB6x", 1, 0x123, 0x7FF, 0, 0))
                self.assertEqual(filters[2][2], struct.pack("<BIIBB6x", 2, 0x123456, 0x1FFFFFFF, 0, 0))
                load = next(c for c in calls if c[0] == 23)
                self.assertEqual(load[2], bytes([5]))
                start = next(c for c in calls if c[0] == 2 and c[2] == struct.pack("<II", 1, 0xC011))
                self.assertLess(calls.index(filters[-1]), calls.index(start))
                self.assertLess(calls.index(load), calls.index(start))
        self.assertTrue(transport.closed)

    def test_new_channel_receives_queue_capacity(self):
        device, channel, transport = fixture()
        device._channels.clear()  # No active reader; force creation through public open.
        with patch("canable.connection.Device.open", return_value=device):
            with open_can(queue_size=17) as connection:
                self.assertEqual(connection.channel._queue.maxsize, 17)

    def test_invalid_options_do_not_open_usb(self):
        cases = [dict(queue_size=0), dict(queue_size=True), dict(channel=-1),
                 dict(bitrate=0), dict(data_bitrate=500000), dict(sample_point=float("nan")),
                 dict(data_sample_point=1), dict(mode="invalid"), dict(one_shot=1),
                 dict(termination=1), dict(bus_load_interval_ms=101),
                 dict(bus_load_interval_ms=10100), dict(filters=[{}]),
                 dict(filters=[CanFilter(1, 0x7FF)] * 9)]
        with patch("canable.connection.Device.open") as opened:
            for kwargs in cases:
                with self.subTest(kwargs=kwargs), self.assertRaises((ValueError, TypeError)):
                    open_can(**kwargs)
            opened.assert_not_called()

    def test_filter_values(self):
        for args in ((-1, 0), (0x800, 0x7FF), (0, 0x800), (True, 0)):
            with self.subTest(args=args), self.assertRaises(ValueError):
                CanFilter(*args)
        self.assertEqual(CanFilter(0x1FFFFFFF, 0, extended=True).mask, 0)

    def test_unsupported_termination_never_configures(self):
        device, channel, transport = fixture()
        with patch("canable.connection.Device.open", return_value=device):
            with self.assertRaises(UnsupportedError):
                open_can(termination=False)
        self.assertTrue(transport.closed)
        self.assertFalse(any(c[0] in (0, 1, 2, 12) for c in transport.calls))

    def test_supported_termination_and_reporting_off(self):
        from dataclasses import replace
        device, channel, transport = fixture()
        channel.info = replace(channel.info, features=channel.info.features | Feature.TERMINATION)
        with patch("canable.connection.Device.open", return_value=device):
            with open_can(termination=True, bus_load_interval_ms=0, filters=[]):
                self.assertIn((12, 0, struct.pack("<I", 1), 0), transport.calls)
                self.assertIn((23, 0, b"\0", 0), transport.calls)
                self.assertIn((21, 0, bytes(17), 0), transport.calls)

    def test_optional_settings_are_not_written_by_default(self):
        device, channel, transport = fixture()
        with patch("canable.connection.Device.open", return_value=device):
            with open_can():
                self.assertFalse(any(c[0] in (12, 21, 23) for c in transport.calls))

    def test_configuration_failure_closes_without_normal_start(self):
        for request, kwargs in ((21, dict(filters=[CanFilter(1, 0x7FF)])),
                                (23, dict(bus_load_interval_ms=100))):
            with self.subTest(request=request):
                device, channel, transport = fixture()
                transport.failed_request = request
                with patch("canable.connection.Device.open", return_value=device):
                    with self.assertRaises(FirmwareError):
                        open_can(**kwargs)
                self.assertTrue(transport.closed)
                self.assertFalse(channel._thread.is_alive())
                self.assertFalse(any(c[0] == 2 and c[2] == struct.pack("<II", 1, 0xC010)
                                     for c in transport.calls))
