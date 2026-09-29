from __future__ import annotations

from dataclasses import dataclass
from enum import IntFlag


class Feature(IntFlag):
    LISTEN_ONLY = 1
    LOOPBACK = 2
    ONE_SHOT = 8
    TIMESTAMP = 0x10
    IDENTIFY = 0x20
    CAN_FD = 0x100
    FD_TIMING = 0x400
    TERMINATION = 0x800
    ERROR_STATE = 0x2000
    ELMUE = 0x4000
    BLOBS = 0x8000


FD_LENGTHS = (0, 1, 2, 3, 4, 5, 6, 7, 8, 12, 16, 20, 24, 32, 48, 64)


@dataclass(frozen=True)
class CanFilter:
    """Accept IDs matching (received_id & mask) == (can_id & mask)."""
    can_id: int
    mask: int
    extended: bool = False

    def __post_init__(self):
        if type(self.extended) is not bool:
            raise TypeError("extended must be bool")
        maximum = 0x1FFFFFFF if self.extended else 0x7FF
        for name in ("can_id", "mask"):
            value = getattr(self, name)
            if type(value) is not int or not 0 <= value <= maximum:
                raise ValueError(f"{name} outside selected 11/29-bit range")


@dataclass(frozen=True)
class Frame:
    arbitration_id: int
    data: bytes = b""
    extended: bool = False
    fd: bool = False
    brs: bool = False
    remote: bool = False
    remote_dlc: int = 0
    esi: bool = False

    def __post_init__(self):
        if not isinstance(self.arbitration_id, int) or not 0 <= self.arbitration_id <= (0x1FFFFFFF if self.extended else 0x7FF):
            raise ValueError("CAN ID outside selected 11/29-bit range")
        if not isinstance(self.data, (bytes, bytearray, memoryview)):
            raise TypeError("data must be bytes-like")
        object.__setattr__(self, "data", bytes(self.data))
        if self.remote:
            if self.fd or self.data or not isinstance(self.remote_dlc, int) or not 0 <= self.remote_dlc <= 8:
                raise ValueError("RTR requires classic CAN, empty data and DLC 0..8")
        elif self.remote_dlc:
            raise ValueError("remote_dlc is only valid for RTR")
        if not self.fd and (len(self.data) > 8 or self.brs or self.esi):
            raise ValueError("classic CAN permits <=8 bytes and no BRS/ESI")
        if self.fd and len(self.data) not in FD_LENGTHS:
            raise ValueError("CAN FD length must be 0..8,12,16,20,24,32,48,64; pad explicitly")


@dataclass(frozen=True)
class DeviceInfo:
    path: str
    serial: str
    product: str
    interface: int
    channel: int | None
    firmware: int | None = None
    hardware: int | None = None
    channel_count: int | None = None
    features: Feature = Feature(0)
    can_clock_hz: int | None = None
    error: str | None = None


@dataclass(frozen=True)
class TimingLimits:
    seg1_min: int
    seg1_max: int
    seg2_min: int
    seg2_max: int
    sjw_max: int
    brp_min: int
    brp_max: int
    brp_inc: int


@dataclass(frozen=True)
class BitTiming:
    brp: int
    seg1: int
    seg2: int
    sjw: int
    clock_hz: int

    @property
    def bitrate(self) -> float:
        return self.clock_hz / (self.brp * (1 + self.seg1 + self.seg2))

    @property
    def sample_point(self) -> float:
        return (1 + self.seg1) / (1 + self.seg1 + self.seg2)


@dataclass(frozen=True)
class Event:
    """One firmware event. Raw bytes retained for audit/future protocol extensions.

    device_timestamp_us is the RAW 32-bit firmware timestamp, not wall time.
    host_timestamp_ns uses perf_counter_ns at USB read completion.
    """
    kind: str
    channel: int
    host_timestamp_ns: int
    raw: bytes
    frame: Frame | None = None
    device_timestamp_us: int | None = None
    marker: int | None = None
    error_id: int = 0
    error_data: bytes = b""
    text: str = ""
    bus_load: int | None = None

    @property
    def app_flags(self) -> int:
        return self.error_data[5] if len(self.error_data) == 8 else 0
