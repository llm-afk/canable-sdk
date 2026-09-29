"""Channel lifecycle, bounded event queue and explicit Tx completion receipts."""
from __future__ import annotations

import queue
import struct
import threading
import time

from .errors import (ProtocolError, QueueOverflow, ReceiveTimeout, SendUncertain,
                     StateError, UnsupportedError)
from .models import Event, Feature, Frame, TimingLimits
from .protocol import MAX_PENDING, choose_timing, decode_transfer, encode_batch


class TxReceipt:
    """USB submission and firmware Tx Event are separate observations.

    wait() timeout does NOT cancel/retry transmission or recycle its marker.
    A firmware Tx Event in one-shot/loopback mode is not proof of remote ACK.
    """
    def __init__(self, marker: int, frame: Frame):
        self.marker, self.frame = marker, frame
        self.submitted_ns: int | None = None
        self.event: Event | None = None
        self.error: Exception | None = None
        self._done = threading.Event()

    def wait(self, timeout: float | None = 1.0) -> Event:
        if not self.marker:
            raise UnsupportedError("echo was disabled; only USB submission is observable")
        if timeout is not None and timeout < 0:
            raise ValueError("timeout must be >=0 or None")
        if not self._done.wait(timeout):
            raise ReceiveTimeout("Tx event not observed; frame may still be pending; no automatic retry")
        if self.error:
            raise self.error
        return self.event

    def _finish(self, *, event=None, error=None):
        if not self._done.is_set():
            self.event, self.error = event, error
            self._done.set()


class Channel:
    def __init__(self, device, index: int, queue_size: int):
        if queue_size <= 0:
            raise ValueError("queue_size must be positive")
        self.device, self.index = device, index
        self.info = device.infos[index]
        self.transport = device._transports[index]
        self._queue = queue.Queue(queue_size)
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread = None
        self._pending: dict[int, TxReceipt] = {}
        self._next_marker = 1
        self._fatal = self._send_fault = None
        self._closed = False
        self.prepared = self.running = self._configured = False
        self._fd = self._listen_only = False
        self._mode = "normal"
        self.rx_events = self.dropped_events = self.submitted_frames = 0
        self.nominal_timing = self.data_timing = None

    def _check(self):
        if self._closed or self.device._closed:
            raise StateError("channel is closed")
        if self._fatal:
            raise self._fatal

    def configure(self, *, bitrate: int = 1_000_000, data_bitrate: int | None = None,
                  sample_point: float = 0.75, data_sample_point: float = 0.75):
        with self._lock:
            self._check()
            if self.running or self._send_fault:
                raise StateError("stop running channel, or reopen faulted device before configuring")
            cap = struct.unpack("<10I", self.device._request(self.index, 4, length=40))
            nominal = choose_timing(cap[1], TimingLimits(*cap[2:]), bitrate, sample_point)
            data = None
            if data_bitrate is not None:
                if data_bitrate < bitrate:
                    raise ValueError("this firmware requires data bitrate >= nominal bitrate")
                if self.info.features & (Feature.CAN_FD | Feature.FD_TIMING) != (Feature.CAN_FD | Feature.FD_TIMING):
                    raise UnsupportedError("firmware has no CAN FD timing capability")
                fd_cap = struct.unpack("<18I", self.device._request(self.index, 11, length=72))
                nominal = choose_timing(fd_cap[1], TimingLimits(*fd_cap[2:10]), bitrate, sample_point)
                data = choose_timing(fd_cap[1], TimingLimits(*fd_cap[10:]), data_bitrate, data_sample_point)
            if not self.info.features & Feature.TIMESTAMP:
                raise UnsupportedError("this SDK requires firmware hardware timestamps")
            if not self.info.features & Feature.LOOPBACK:
                raise UnsupportedError("internal loopback capability required to normalize stopped-channel configuration")
            self._configured = False
            self._reset()
            self.prepared = True
            try:
                self.device._request(self.index, 0, data=struct.pack("<I", 0xBEEF))
                # Firmware can_close() does nothing when already closed. Thus a
                # prior configured-but-never-started FD bitrate can survive reset.
                # Normalize with an INTERNAL loopback start/stop, transmitting no
                # packets and no external ACK. Use a <=1M dummy timing supported
                # by all boards in this revision, overwriting any stale FD timing.
                dummy = choose_timing(cap[1], TimingLimits(*cap[2:]), 1_000_000)
                self._set_timing(1, dummy)
                if self.info.features & Feature.FD_TIMING:
                    fd_limits = struct.unpack("<18I", self.device._request(self.index, 11, length=72))
                    self._set_timing(10, choose_timing(fd_limits[1], TimingLimits(*fd_limits[10:]), 1_000_000))
                self.device._request(self.index, 2, data=struct.pack("<II", 1, 0x4013))
                self._reset()  # Now can_close() really clears all timing/filters.
                for request, timing in ((1, nominal), (10, data)):
                    if timing is not None:
                        self._set_timing(request, timing)
            except Exception as exc:
                self._fatal = exc
                # Closing still attempts reset, even when initialization failed.
                raise
            self.nominal_timing, self.data_timing = nominal, data
            self._fd, self._configured = data is not None, True
            if self._thread is None:
                self._thread = threading.Thread(target=self._reader, name=f"canable-rx-{self.index}", daemon=True)
                self._thread.start()
            return self

    def _set_timing(self, request, timing):
        self.device._request(self.index, request, data=struct.pack("<5I", 0, timing.seg1, timing.seg2, timing.sjw, timing.brp))

    def _reset(self):
        # Keep wire timestamp format identical before/after start/stop.
        self.device._request(self.index, 2, data=struct.pack("<II", 0, int(Feature.ELMUE | Feature.TIMESTAMP)))

    def start(self, *, mode: str = "normal", one_shot: bool = False):
        with self._lock:
            self._check()
            if self.running or not self._configured or self._send_fault:
                raise StateError("configure a stopped healthy channel before start")
            modes = {"normal": 0, "listen_only": 1, "internal_loopback": 3, "external_loopback": 2}
            if mode not in modes:
                raise ValueError(f"mode must be one of {tuple(modes)}")
            flags = Feature.ELMUE | Feature.TIMESTAMP | modes[mode]
            if one_shot:
                flags |= Feature.ONE_SHOT
            if self.info.features & Feature.BLOBS:
                flags |= Feature.BLOBS
            if int(flags) & ~int(self.info.features):
                raise UnsupportedError("requested mode is not advertised by firmware")
            self.device._request(self.index, 2, data=struct.pack("<II", 1, int(flags)))
            self.running, self._listen_only, self._mode = True, mode == "listen_only", mode
            return self

    def stop(self):
        with self._lock:
            if self._closed:
                return
            if self.prepared:
                self._reset()
            self.running = self._configured = False
            if self._pending:
                # Do not recycle unresolved markers across reset and risk
                # attributing a delayed echo to a new send.
                self._send_fault = SendUncertain("channel stopped with pending transmissions; reopen device")
                self._fail_pending(self._send_fault)

    def send(self, frame: Frame, *, echo: bool = True) -> TxReceipt:
        return self.send_many([frame], echo=echo)[0]

    def send_many(self, frames, *, echo: bool = True) -> tuple[TxReceipt, ...]:
        frames = list(frames)
        # Validate before touching transport or reserving markers.
        encode_batch(frames, [0] * len(frames))
        with self._lock:
            self._check()
            if self._send_fault:
                raise self._send_fault
            if not self.running or self._listen_only:
                raise StateError("send requires a running transmit-capable channel")
            if any(f.fd for f in frames) and not self._fd:
                raise StateError("configure a data bitrate before sending CAN FD")
            if any(f.brs for f in frames) and self.data_timing.bitrate <= self.nominal_timing.bitrate:
                raise ValueError("BRS requires data bitrate > nominal bitrate; firmware would otherwise clear BRS")
            if len(frames) > 1 and not self.info.features & Feature.BLOBS:
                raise UnsupportedError("firmware does not advertise batch support")
            if echo and len(self._pending) + len(frames) > MAX_PENDING:
                raise QueueOverflow("63 Tx receipts outstanding; wait for completion before sending (one firmware slot reserved)")
            receipts = []
            for frame in frames:
                marker = 0
                if echo:
                    while self._next_marker in self._pending:
                        self._next_marker = self._next_marker % 255 + 1
                    marker = self._next_marker
                    self._next_marker = marker % 255 + 1
                receipt = TxReceipt(marker, frame)
                if marker:
                    self._pending[marker] = receipt
                receipts.append(receipt)
            try:
                self.transport.write(encode_batch(frames, [r.marker for r in receipts]))
            except Exception as exc:
                self._send_fault = SendUncertain(f"USB send incomplete: {exc}; reopen device; do not replay automatically")
                self._fail_pending(self._send_fault)
                raise self._send_fault from exc
            now = time.perf_counter_ns()
            for receipt in receipts:
                receipt.submitted_ns = now
            self.submitted_frames += len(frames)
            return tuple(receipts)

    def receive(self, timeout: float | None = 1.0) -> Event | None:
        """Return next event (including errors/debug/Tx echo), or None on timeout.

        Exactly one consumer should drain this queue. Fatal errors take priority
        over queued data so an overflow/disconnect cannot look like normal input.
        """
        if timeout is not None and timeout < 0:
            raise ValueError("timeout must be >=0 or None")
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            self._check()
            remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
            try:
                event = self._queue.get(timeout=min(0.05, remaining) if remaining is not None else 0.05)
                self._check()
                return event
            except queue.Empty:
                if deadline is not None and time.monotonic() >= deadline:
                    self._check()
                    return None

    def _reader(self):
        try:
            while not self._stop_event.is_set():
                raw = self.transport.read()
                if not raw:
                    continue
                events = decode_transfer(raw, self.index, time.perf_counter_ns())
                for event in events:
                    with self._lock:
                        if event.kind == "tx_echo":
                            receipt = self._pending.pop(event.marker, None)
                            if receipt:
                                receipt._finish(event=event)
                        if event.kind == "error" and (event.app_flags & 0x1F or event.error_id & 0x40):
                            self._send_fault = SendUncertain("firmware Tx failure/overflow/timeout/bus-off; inspect error event and reopen device")
                            self._fail_pending(self._send_fault)
                        self.rx_events += 1
                        try:
                            self._queue.put_nowait(event)
                        except queue.Full:
                            self.dropped_events += 1
                            raise QueueOverflow("host event queue full; drain faster or increase queue_size; reopen device")
        except Exception as exc:
            if not self._stop_event.is_set():
                with self._lock:
                    self._fatal = exc
                    self._fail_pending(SendUncertain(f"receiver failed; transmit results unknown: {exc}"))

    def _fail_pending(self, exc):
        for receipt in self._pending.values():
            receipt._finish(error=exc)
        # Retain occupied markers until object destruction; no unsafe reuse.

    def _control_ready(self, *, stopped=False):
        self._check()
        if not self.prepared or (stopped and self.running):
            raise StateError("configure first; this operation may require a stopped channel")

    def identify(self, enabled: bool = True):
        self.device._request(self.index, 7, data=struct.pack("<I", bool(enabled)))

    def error_state(self) -> dict:
        if not self.info.features & Feature.ERROR_STATE:
            raise UnsupportedError("error-state polling not supported")
        state, rx, tx = struct.unpack("<III", self.device._request(self.index, 14, length=12))
        return dict(state=state, rx_errors=rx, tx_errors=tx)

    def set_termination(self, enabled: bool):
        if not self.info.features & Feature.TERMINATION:
            raise UnsupportedError("board has no software-controlled termination")
        self.device._request(self.index, 12, data=struct.pack("<I", bool(enabled)))

    def get_termination(self) -> bool:
        if not self.info.features & Feature.TERMINATION:
            raise UnsupportedError("board has no software-controlled termination")
        return bool(struct.unpack("<I", self.device._request(self.index, 13, length=4))[0])

    def set_bus_load_interval(self, milliseconds: int = 1000):
        self._control_ready()
        if not isinstance(milliseconds, int) or not 0 <= milliseconds <= 10000 or milliseconds % 100:
            raise ValueError("bus load interval must be 0 (off) or 100..10000 ms in 100 ms steps")
        self.device._request(self.index, 23, data=bytes([milliseconds // 100]))

    def clear_filters(self):
        with self._lock:
            self._control_ready(stopped=True)
            self.device._request(self.index, 21, data=bytes(17))

    def add_filter(self, can_id: int, mask: int, *, extended: bool = False):
        with self._lock:
            self._control_ready(stopped=True)
            self._filter(2 if extended else 1, can_id, mask, extended)

    def _filter(self, operation, can_id, mask, extended, index=0, destination=0):
        maximum = 0x1FFFFFFF if extended else 0x7FF
        if not 0 <= can_id <= maximum or not 0 <= mask <= maximum:
            raise ValueError("filter ID/mask outside selected 11/29-bit range")
        self.device._request(self.index, 21, data=struct.pack("<BIIBB6x", operation, can_id, mask, index, destination))

    def set_bridge_filter(self, index: int, destination: int, can_id: int, mask: int,
                          *, extended: bool = False, block: bool = False):
        with self._lock:
            self._control_ready(stopped=True)
            if destination == self.index or destination not in self.device.infos or not 0 <= index < 20:
                raise ValueError("bridge requires another channel and filter index 0..19")
            self._filter((13 if block else 11) + int(extended), can_id, mask, extended, index, destination)

    def clear_bridge_filters(self, index: int = 255):
        with self._lock:
            self._control_ready(stopped=True)
            if index != 255 and not 0 <= index < 20:
                raise ValueError("bridge index must be 0..19 or 255 (all)")
            self._filter(10, 0, 0, False, index)

    def close(self):
        with self._lock:
            if self._closed:
                return
            failure = None
            try:
                self.stop()
            except Exception as exc:
                failure = exc
            self._stop_event.set()
            self._fail_pending(SendUncertain("channel closed before Tx event observed"))
        self.transport.abort_read()
        if self._thread:
            self._thread.join(2.0)
            if self._thread.is_alive():
                raise StateError("reader did not stop; transport retained for a later close()")
        self._closed, self.running = True, False
        if failure:
            raise failure
