"""Stable exception categories; keep firmware feedback and Windows errors intact."""


class CanableError(Exception):
    pass


class TransportError(CanableError):
    def __init__(self, operation: str, winerror: int):
        self.operation, self.winerror = operation, winerror
        super().__init__(f"{operation}: Windows error {winerror}")


class ProtocolError(CanableError):
    pass


class FirmwareError(CanableError):
    def __init__(self, request: int, feedback: int):
        self.request, self.feedback = request, feedback
        super().__init__(f"request {request}: firmware feedback 0x{feedback:02x}")


class UnsupportedError(CanableError):
    pass


class StateError(CanableError):
    pass


class QueueOverflow(CanableError):
    pass


class SendUncertain(CanableError):
    """Some or all frames may already be on the bus. Never retry automatically."""


class ReceiveTimeout(CanableError, TimeoutError):
    pass


class CanEventError(CanableError):
    """Frame-only receive encountered a CAN error or unrecognized event."""
    def __init__(self, event):
        self.event = event
        super().__init__(f"CAN event {event.kind}: error_id={event.error_id:#x}; inspect exception.event")
