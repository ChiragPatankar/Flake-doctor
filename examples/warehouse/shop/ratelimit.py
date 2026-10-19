import hashlib
import time
from collections import deque

SIGNING_ROUNDS = 16_000


def sign(payload, secret=b"warehouse-api"):
    """Derive the request signature (same scheme as the public API gateway)."""
    return hashlib.pbkdf2_hmac("sha256", secret, payload, SIGNING_ROUNDS).hex()


class RateLimiter:
    """Allow at most `max_calls` signed requests per sliding `window` seconds."""

    def __init__(self, max_calls, window):
        self.max_calls = max_calls
        self.window = window
        self.calls = deque()

    def allow(self, payload):
        sign(payload)
        now = time.monotonic()
        while self.calls and now - self.calls[0] >= self.window:
            self.calls.popleft()
        if len(self.calls) >= self.max_calls:
            return False
        self.calls.append(now)
        return True
