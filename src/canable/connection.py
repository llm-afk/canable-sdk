"""Small application facade over the original Device/Channel contracts."""
import math
import threading
import time
from .device import Device, discover
from .errors import StateError, CanEventError

class Connection:
    """One connection, one receive consumer and one chosen receive mode.

    recv() returns frames; recv_event()/legacy receive() return every event.
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
             data_sample_point=0.75, one_shot=False):
        device = Device.open(serial=serial)
        try:
            ch = device.channel(channel)
            ch.configure(bitrate=bitrate, data_bitrate=data_bitrate,
                         sample_point=sample_point, data_sample_point=data_sample_point)
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
        """Compatibility alias for recv_event(); it still returns Event."""
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

def open_can(*, serial=None, **kwargs):
    return Connection.open(serial=serial, **kwargs)

def list_devices():
    """Read-only discovery. One DeviceInfo per CAN interface, including failures.

    Multi-channel adapters share a serial. Busy/inaccessible entries retain error.
    """
    return discover()
