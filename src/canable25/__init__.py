"""CANable 2.5 Python SDK. Import is safe without Windows or attached hardware."""
from .device import Device, discover
from .channel import Channel, TxReceipt
from .models import BitTiming, DeviceInfo, Event, Feature, Frame
from .errors import (CanableError, FirmwareError, ProtocolError, QueueOverflow,
                     ReceiveTimeout, SendUncertain, StateError, TransportError, UnsupportedError)

__version__ = "0.1.1"
__all__ = ["Device", "discover", "Channel", "TxReceipt", "Frame", "Event", "DeviceInfo", "Feature",
           "BitTiming", "CanableError", "FirmwareError", "ProtocolError", "QueueOverflow", "ReceiveTimeout",
           "SendUncertain", "StateError", "TransportError", "UnsupportedError"]
