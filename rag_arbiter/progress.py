"""Framework-independent progress and cooperative cancellation."""
from typing import Protocol


class RunCancelled(BaseException):
    """Like asyncio.CancelledError: must not be swallowed as a document failure."""


class ProgressReporter(Protocol):
    def check(self): ...
    def emit(self, event_type: str, stage: str = "", message: str = "", **metrics): ...


class ConsoleReporter:
    def check(self):
        pass

    def emit(self, event_type, stage="", message="", **metrics):
        self.check()
        if event_type == "stage_started":
            print(f"[{stage}] {message}")
