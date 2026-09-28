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
