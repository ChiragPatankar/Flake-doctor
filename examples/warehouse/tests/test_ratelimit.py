import time

from shop.ratelimit import RateLimiter, sign


def test_signature_is_stable():
    assert sign(b"GET /orders") == sign(b"GET /orders")
    assert sign(b"GET /orders") != sign(b"GET /users")


def test_blocks_fourth_call():
    limiter = RateLimiter(max_calls=3, window=0.02)
    results = [limiter.allow(b"GET /orders") for _ in range(4)]
    assert results == [True, True, True, False]


def test_allows_again_after_window():
    limiter = RateLimiter(max_calls=1, window=0.02)
    assert limiter.allow(b"GET /orders")
    time.sleep(0.03)
    assert limiter.allow(b"GET /orders")
