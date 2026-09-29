import unittest
from unittest.mock import Mock, patch
from canable import Connection, open_can, Frame, StateError

class ConnectionTests(unittest.TestCase):
    def test_open_default_selection(self):
        device = Mock()
        with patch("canable.connection.Device.open", return_value=device) as opened:
            with open_can(data_bitrate=4_000_000) as connection:
                opened.assert_called_once_with(serial=None)
                device.channel.assert_called_once_with(0, queue_size=4096)
                device.channel.return_value.configure.assert_called_once_with(
                    bitrate=1_000_000, data_bitrate=4_000_000,
                    sample_point=0.75, data_sample_point=0.75)
                connection.send_many([Frame(1)])
                device.channel.return_value.send_many.assert_called_once()
            device.close.assert_called_once()
    def test_initialization_failure_releases(self):
        device = Mock()
        device.channel.return_value.configure.side_effect = ValueError("timing")
        with patch("canable.connection.Device.open", return_value=device):
            with self.assertRaises(ValueError):
                open_can()
        device.close.assert_called_once()
    def test_event_and_receipt_preserved(self):
        device, channel = Mock(), Mock()
        connection = Connection(device, channel)
        self.assertIs(connection.receive(0), channel.receive.return_value)
        self.assertIs(connection.send(Frame(1)), channel.send.return_value)
    def test_close_idempotent(self):
        device = Mock()
        connection = Connection(device, Mock())
        connection.close()
        connection.close()
        device.close.assert_called_once()
        with self.assertRaises(StateError):
            connection.__enter__()
    def test_timeout_validation(self):
        connection = Connection(Mock(), Mock())
        for timeout in (-1, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                connection.receive(timeout)

class SimpleReceiveTests(unittest.TestCase):
    def event(self, kind, **kwargs):
        from canable import Event
        return Event(kind, 0, 123, b"", **kwargs)

    def test_frame_receive_skips_echo_and_keeps_metadata(self):
        channel = Mock()
        frame = Frame(0x123, b"abc")
        debug = self.event("debug", text="test")
        load = self.event("bus_load", bus_load=12)
        channel.receive.side_effect = [self.event("tx_echo"), debug, load, self.event("frame", frame=frame)]
        connection = Connection(Mock(), channel)
        self.assertIs(connection.recv(0), frame)
        self.assertIs(connection.last_debug_event, debug)
        self.assertIs(connection.last_bus_load_event, load)

    def test_error_is_not_silently_dropped(self):
        from canable import CanEventError
        channel = Mock()
        event = self.event("error", error_id=0x40)
        channel.receive.return_value = event
        connection = Connection(Mock(), channel)
        with self.assertRaises(CanEventError) as cm:
            connection.recv(0)
        self.assertIs(cm.exception.event, event)

    def test_unknown_event_visible(self):
        from canable import CanEventError
        channel = Mock()
        channel.receive.return_value = self.event("unknown")
        with self.assertRaises(CanEventError):
            Connection(Mock(), channel).recv(0)

    def test_receive_modes_cannot_mix(self):
        from canable import StateError
        for first, second in (("recv", "recv_event"), ("recv_event", "recv"), ("receive", "recv")):
            channel = Mock()
            channel.receive.return_value = None
            connection = Connection(Mock(), channel)
            getattr(connection, first)(0)
            with self.assertRaises(StateError):
                getattr(connection, second)(0)
            self.assertEqual(channel.receive.call_count, 1)

    def test_concurrent_consumers_rejected(self):
        import threading
        from canable import StateError
        entered, release = threading.Event(), threading.Event()
        channel = Mock()
        def receive(timeout):
            entered.set()
            release.wait(1)
        channel.receive.side_effect = receive
        connection = Connection(Mock(), channel)
        thread = threading.Thread(target=lambda: connection.recv(1))
        thread.start()
        try:
            self.assertTrue(entered.wait(1))
            with self.assertRaises(StateError):
                connection.recv(0)
        finally:
            release.set()
            thread.join(1)
        self.assertFalse(thread.is_alive())

    def test_status_and_cached_info(self):
        channel = Mock()
        channel.error_state.return_value = {"state":4,"rx_errors":0,"tx_errors":0}
        connection = Connection(Mock(), channel)
        self.assertIs(connection.info, channel.info)
        channel.error_state.assert_not_called()
        self.assertEqual(connection.read_status()["state"], 4)
        channel.error_state.assert_called_once_with()
        connection.identify()
        channel.identify.assert_called_once_with(True)

    def test_list_devices_preserves_errors(self):
        from canable import list_devices
        entries = [Mock(serial="x", channel=0), Mock(error="busy")]
        with patch("canable.connection.discover", return_value=entries) as discover:
            self.assertIs(list_devices(), entries)
            discover.assert_called_once_with()

    def test_closed_facade_rejects_send(self):
        from canable import StateError
        channel = Mock()
        connection = Connection(Mock(), channel)
        connection.close()
        with self.assertRaises(StateError):
            connection.send(Frame(1))
        channel.send.assert_not_called()

    def test_failed_close_can_retry(self):
        device = Mock()
        device.close.side_effect = [RuntimeError("busy"), None]
        connection = Connection(device, Mock())
        with self.assertRaises(RuntimeError):
            connection.close()
        connection.close()
        self.assertEqual(device.close.call_count, 2)
