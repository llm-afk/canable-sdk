"""Pure Elmue 2026-08-03 wire codec; no USB, threads or application protocols.

Wire definitions: Firmware/Candlelight/candlelight_def.h and control.c.
"""
import struct

from .errors import ProtocolError
from .models import BitTiming, Event, Frame, TimingLimits

MAX_BLOB = 2048
MAX_PENDING = 63  # Firmware reports overflow on an entirely exhausted 64-slot pool.
MIN_FIRMWARE = 0x260803


def encode_frame(frame: Frame, marker: int = 0) -> bytes:
    if not 0 <= marker <= 255:
        raise ValueError("marker must be 0..255")
    if frame.esi:
        raise ValueError("ESI is receive-only; controller determines transmit error state")
    payload = bytes([frame.remote_dlc]) if frame.remote else frame.data
    flags = (2 if frame.fd else 0) | (4 if frame.brs else 0)
    can_id = frame.arbitration_id | (0x80000000 if frame.extended else 0) | (0x40000000 if frame.remote else 0)
    return struct.pack("<BB B I B", 8 + len(payload), 10, flags, can_id, marker) + payload


def encode_batch(frames: list[Frame], markers: list[int]) -> bytes:
    if not 1 <= len(frames) <= MAX_PENDING or len(frames) != len(markers):
        raise ValueError("batch requires 1..63 frames and one marker per frame (one firmware slot reserved)")
    packets = [encode_frame(f, m) for f, m in zip(frames, markers)]
    data = packets[0] if len(packets) == 1 else bytes([len(packets), 16]) + b"".join(packets)
    if len(data) > MAX_BLOB:
        raise ValueError("batch exceeds 2048 bytes; split explicitly (partial sends are not atomic)")
    # Firmware buf_store_tx_packet() copies 64 bytes even for short frames.
    # A mixed-size blob can fit on the wire yet make its final fixed copy cross
    # the device's 2048-byte input buffer. Leave room for every fixed copy.
    offset = 0 if len(packets) == 1 else 2
    for packet in packets:
        if offset + 8 + 64 > MAX_BLOB:
            raise ValueError("mixed batch would exceed firmware fixed-copy buffer boundary; split batch")
        offset += len(packet)
    return data


def decode_transfer(data: bytes, channel: int, host_ns: int, timestamps: bool = True) -> list[Event]:
    """Decode a completed USB transfer; reject truncated/trailing/nested blobs."""
    if not 2 <= len(data) <= MAX_BLOB:
        raise ProtocolError("USB transfer length outside 2..2048")
    if data[1] == 17:
        count, offset = data[0], 2
        if not count:
            raise ProtocolError("empty Rx blob")
    else:
        count, offset = 1, 0
    events = []
    for _ in range(count):
        if offset + 2 > len(data):
            raise ProtocolError("truncated header")
        size = data[offset]
        if size < 2 or offset + size > len(data):
            raise ProtocolError("invalid frame size")
        raw = data[offset:offset + size]
        events.append(_decode_frame(raw, channel, host_ns, timestamps))
        offset += size
    if offset != len(data):
        raise ProtocolError("trailing bytes / incorrect blob count")
    return events


def _decode_frame(raw: bytes, channel: int, host_ns: int, timestamps: bool) -> Event:
    kind, stamp = raw[1], None
    base = dict(channel=channel, host_timestamp_ns=host_ns, raw=raw)
    extra = 4 if timestamps else 0
    if kind == 12:
        if len(raw) < 7 + extra:
            raise ProtocolError("short Rx frame")
        flags, can_id = struct.unpack_from("<BI", raw, 2)
        if flags & ~0x0E or can_id & 0x20000000:
            raise ProtocolError("invalid Rx flags")
        if timestamps:
            stamp = struct.unpack_from("<I", raw, 7)[0]
        payload = raw[7 + extra:]
        remote = bool(can_id & 0x40000000)
        if remote and len(payload) != 1:
            raise ProtocolError("RTR must carry exactly one DLC byte")
        try:
            frame = Frame(can_id & 0x1FFFFFFF, b"" if remote else payload,
                          extended=bool(can_id & 0x80000000), fd=bool(flags & 2),
                          brs=bool(flags & 4), esi=bool(flags & 8), remote=remote,
                          remote_dlc=payload[0] if remote else 0)
        except (ValueError, TypeError) as exc:
            raise ProtocolError(str(exc)) from exc
        return Event("frame", frame=frame, device_timestamp_us=stamp, **base)
    if kind == 11:
        if len(raw) != 3 + extra or not raw[2]:
            raise ProtocolError("invalid Tx echo")
        if timestamps:
            stamp = struct.unpack_from("<I", raw, 3)[0]
        return Event("tx_echo", marker=raw[2], device_timestamp_us=stamp, **base)
    if kind == 13:
        if len(raw) != 14 + extra:
            raise ProtocolError("invalid error event")
        if timestamps:
            stamp = struct.unpack_from("<I", raw, 14)[0]
        return Event("error", error_id=struct.unpack_from("<I", raw, 2)[0],
                     error_data=raw[6:14], device_timestamp_us=stamp, **base)
    if kind == 14:
        return Event("debug", text=raw[2:].rstrip(b"\0").decode("ascii", errors="replace"), **base)
    if kind == 15:
        if len(raw) != 3 or raw[2] > 100:
            raise ProtocolError("invalid bus load event")
        return Event("bus_load", bus_load=raw[2], **base)
    if kind in (10, 16, 17):
        raise ProtocolError("host-only or nested blob in device stream")
    return Event("unknown", **base)


def choose_timing(clock: int, limits: TimingLimits, bitrate: int,
                  sample_point: float = 0.75) -> BitTiming:
    """Exact bitrate only. Select nearest sample point, then greatest TQ count."""
    if not isinstance(bitrate, int) or bitrate <= 0 or not 0 < sample_point < 1:
        raise ValueError("positive integer bitrate and sample point in (0,1) required")
    if not (0 < clock <= 1_000_000_000 and 1 <= limits.brp_min <= limits.brp_max <= 65536
            and 1 <= limits.brp_inc <= 65536 and 1 <= limits.seg1_min <= limits.seg1_max <= 65536
            and 1 <= limits.seg2_min <= limits.seg2_max <= 65536 and limits.sjw_max >= 1):
        raise ProtocolError("invalid timing capabilities")
    best = None
    for brp in range(limits.brp_min, limits.brp_max + 1, limits.brp_inc):
        if clock % (brp * bitrate):
            continue
        tq = clock // (brp * bitrate)
        low = max(limits.seg2_min, tq - 1 - limits.seg1_max)
        high = min(limits.seg2_max, tq - 1 - limits.seg1_min)
        if low > high:
            continue
        seg2 = max(low, min(high, round(tq * (1 - sample_point))))
        seg1 = tq - 1 - seg2
        timing = BitTiming(brp, seg1, seg2, min(seg1, seg2, limits.sjw_max), clock)
        rank = (abs(timing.sample_point - sample_point), -tq, brp)
        if best is None or rank < best[0]:
            best = rank, timing
    if best is None:
        raise ValueError(f"cannot realize exact bitrate {bitrate} from clock {clock}")
    return best[1]
