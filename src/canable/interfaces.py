"""Backend-neutral application contract; future SLCAN can implement this Protocol.

Device-specific maintenance remains on the concrete Candlelight Device.
"""
from typing import Iterable, Protocol

from .channel import TxReceipt
from .models import Event, Frame


class CanChannel(Protocol):
    def configure(self, *, bitrate: int = 1_000_000, data_bitrate: int | None = None,
                  sample_point: float = 0.75, data_sample_point: float = 0.75): ...

    def start(self, *, mode: str = "normal", one_shot: bool = False): ...

    def send(self, frame: Frame, *, echo: bool = True) -> TxReceipt: ...

    def send_many(self, frames: Iterable[Frame], *, echo: bool = True) -> tuple[TxReceipt, ...]: ...

    def receive(self, timeout: float | None = 1.0) -> Event | None: ...

    def stop(self): ...

    def close(self): ...
