"""CANable 2.5 Python SDK. Import is safe without Windows or attached hardware."""
from .device import Device, discover
from .channel import Channel, TxReceipt
from .models import BitTiming, DeviceInfo, Event, Feature, Frame, CanFilter
from .errors import (CanableError, FirmwareError, ProtocolError, QueueOverflow,
                     ReceiveTimeout, SendUncertain, StateError, TransportError, UnsupportedError)

__version__ = "0.1.5"
__all__ = ["Device", "discover", "Channel", "TxReceipt", "Frame", "CanFilter", "Event", "DeviceInfo", "Feature",
           "BitTiming", "CanableError", "FirmwareError", "ProtocolError", "QueueOverflow", "ReceiveTimeout",
           "SendUncertain", "StateError", "TransportError", "UnsupportedError"]

from .connection import Connection, open_can
__all__ += ["Connection", "open_can"]

from .connection import list_devices
from .errors import CanEventError
__all__ += ["list_devices", "CanEventError"]
