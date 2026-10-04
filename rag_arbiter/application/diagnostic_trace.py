"""Request-local observation only; never feeds captured data back into decisions."""
from contextvars import ContextVar
from functools import wraps

events = ContextVar('chat_diagnostic_events', default=None)


def observed_chat(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        token = events.set([])
        try:
            return method(self, *args, **kwargs)
        finally:
            events.reset(token)
    return wrapped


def record(kind, **data):
    target = events.get()
    if target is not None:
        try:
            from .chat_diagnostic import redact
            target.append({'kind': kind, **redact(data)})
        except Exception:
            # Export failure must never change a retrieval/generation decision.
            target.append({'kind': kind, 'diagnostic_error': 'CAPTURE_UNAVAILABLE'})
