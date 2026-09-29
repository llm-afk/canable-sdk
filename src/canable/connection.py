"""Application connection API for CANable devices and channels."""
import math
import threading
import time
from .device import Device, discover
from .errors import StateError, CanEventError, UnsupportedError
from .models import CanFilter, Feature

class Connection:
    """One connection, one receive consumer and one chosen receive mode.

    recv() returns frames; recv_event()/receive() return every event.
    Mixing the modes is rejected before consuming the queue. No extra RX thread.
    Advanced direct channel reads must not be mixed with this facade.
    """
    def __init__(self, device, channel):
        self.device, self.channel = device, channel
        self._closed = False
        self._rx_lock = threading.Lock()
        self._receive_mode = None
        self.last_debug_event = None
        self.last_bus_load_event = None

    @classmethod
    def open(cls, *, serial=None, channel=0, bitrate=1_000_000,
             data_bitrate=None, mode="normal", sample_point=0.75,
             data_sample_point=0.75, one_shot=False, queue_size=4096,
             filters=None, termination=None, bus_load_interval_ms=None):
        # Validate application input before opening or configuring any hardware.
        for name, value in (("channel", channel), ("queue_size", queue_size),
                            ("bitrate", bitrate)):
            if type(value) is not int or value < (0 if name == "channel" else 1):
                raise ValueError(f"{name} must be an integer >= {0 if name == 'channel' else 1}")
        if data_bitrate is not None and (type(data_bitrate) is not int or data_bitrate < bitrate):
            raise ValueError("data_bitrate must be an integer >= bitrate")
        for value in (sample_point, data_sample_point):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 < value < 1:
                raise ValueError("sample points must be finite and between 0 and 1")
        if mode not in ("normal", "listen_only", "internal_loopback", "external_loopback"):
            raise ValueError("invalid CAN mode")
        if type(one_shot) is not bool or (termination is not None and type(termination) is not bool):
            raise TypeError("one_shot and termination must be bool (termination also accepts None)")
        if bus_load_interval_ms is not None and (
                type(bus_load_interval_ms) is not int or
                not 0 <= bus_load_interval_ms <= 10000 or bus_load_interval_ms % 100):
            raise ValueError("bus_load_interval_ms must be 0 or 100..10000 in 100 ms steps")
        if filters is not None:
            filters = tuple(filters)
            if len(filters) > 8:
                raise ValueError("firmware supports at most 8 acceptance filters")
            if any(not isinstance(rule, CanFilter) for rule in filters):
                raise TypeError("filters must contain CanFilter instances")
        device = Device.open(serial=serial)
        try:
            ch = device.channel(channel, queue_size=queue_size)
            if termination is not None and not ch.info.features & Feature.TERMINATION:
                raise UnsupportedError("board has no software-controlled termination")
            ch.configure(bitrate=bitrate, data_bitrate=data_bitrate,
                         sample_point=sample_point, data_sample_point=data_sample_point)
            if filters is not None:
                ch.clear_filters()
                for rule in filters:
                    ch.add_filter(rule.can_id, rule.mask, extended=rule.extended)
            if termination is not None:
                ch.set_termination(termination)
            if bus_load_interval_ms is not None:
                ch.set_bus_load_interval(bus_load_interval_ms)
            ch.start(mode=mode, one_shot=one_shot)
            return cls(device, ch)
        except BaseException:
            device.close()
            raise

    def _check(self):
        if self._closed:
            raise StateError("connection closed")

    @property
    def info(self):
        """Cached channel/device identity and capabilities; no USB request."""
        self._check()
        return self.channel.info

    def read_status(self):
        """Query controller state and error counters; does not send a CAN frame."""
        self._check()
        return self.channel.error_state()

    def identify(self, enabled=True):
        """Control the adapter identification LED."""
        self._check()
        return self.channel.identify(enabled)

    def send(self, frame, *, echo=True):
        """Submit one frame; returns TxReceipt, not a remote-node acknowledgement."""
        self._check()
        return self.channel.send(frame, echo=echo)

    def send_many(self, frames, *, echo=True):
        """Submit one batch; no automatic split or retry."""
        self._check()
        return self.channel.send_many(frames, echo=echo)

    def _begin_receive(self, mode, timeout):
        self._check()
        if timeout is not None and (not math.isfinite(timeout) or timeout < 0):
            raise ValueError("timeout must be finite and >=0, or None")
        if not self._rx_lock.acquire(blocking=False):
            raise StateError("only one receive consumer is allowed")
        if self._receive_mode not in (None, mode):
            self._rx_lock.release()
            raise StateError("recv() and recv_event()/receive() cannot share a connection")
        self._receive_mode = mode

    def recv_event(self, timeout=1.0):
        """Return the next full Event, or None. Preserves error/echo/debug events."""
        self._begin_receive("events", timeout)
        try:
            return self.channel.receive(timeout)
        finally:
            self._rx_lock.release()

    def receive(self, timeout=1.0):
        """Alias for recv_event(); returns Event."""
        return self.recv_event(timeout)

    def recv(self, timeout=1.0):
        """Return the next Frame or None; error/unknown events raise CanEventError.

        Tx echo is already routed to TxReceipt by Channel. Latest debug/load events
        are retained in last_debug_event/last_bus_load_event, not silently printed.
        Use event mode from the start for full diagnostics and frame timestamps.
        """
        self._begin_receive("frames", timeout)
        deadline = None if timeout is None else time.monotonic() + timeout
        try:
            # Bound even a zero-time poll when metadata traffic never becomes idle.
            for _ in range(4096):
                left = None if deadline is None else max(0, deadline - time.monotonic())
                event = self.channel.receive(left)
                if event is None:
                    return None
                if event.kind == "frame":
                    return event.frame
                if event.kind == "tx_echo":
                    pass
                elif event.kind == "debug":
                    self.last_debug_event = event
                elif event.kind == "bus_load":
                    self.last_bus_load_event = event
                else:
                    raise CanEventError(event)
                if timeout != 0 and deadline is not None and time.monotonic() >= deadline:
                    return None
            raise StateError("too many metadata events in one recv(); drain with another call")
        finally:
            self._rx_lock.release()

    def close(self):
        if not self._closed:
            # Preserve retry after a failed low-level close.
            self.device.close()
            self._closed = True

    def __enter__(self):
        self._check()
        return self

    def __exit__(self, *exc):
        self.close()

def open_can(*, serial=None, channel=0, bitrate=1_000_000,
             data_bitrate=None, mode="normal", sample_point=0.75,
             data_sample_point=0.75, one_shot=False, queue_size=4096,
             filters=None, termination=None, bus_load_interval_ms=None):
    """Configure a channel before starting it; close with a context manager.

    filters: up to eight CanFilter rules; None/empty accepts all IDs.
    termination: None leaves the resistor unchanged; bool requires board support.
    bus_load_interval_ms: None leaves the firmware default; 0 disables reports;
        100..10000 enables reports at multiples of 100 ms.
    queue_size: host event queue capacity (not a firmware transmit buffer).
    """
    return Connection.open(
        serial=serial, channel=channel, bitrate=bitrate,
        data_bitrate=data_bitrate, mode=mode, sample_point=sample_point,
        data_sample_point=data_sample_point, one_shot=one_shot,
        queue_size=queue_size, filters=filters, termination=termination,
        bus_load_interval_ms=bus_load_interval_ms)

def list_devices():
    """Read-only discovery. One DeviceInfo per CAN interface, including failures.

    Multi-channel adapters share a serial. Busy/inaccessible entries retain error.
    """
    return discover()
