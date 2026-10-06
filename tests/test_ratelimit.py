"""P4-B 固定窗口限流单测（零外部服务）。"""
from __future__ import annotations

import time

from web.backend.ratelimit import FixedWindowLimiter


def test_limit_zero_disables_limiter():
    limiter = FixedWindowLimiter(0)
    assert all(limiter.allow("k") for _ in range(100))


def test_allows_up_to_limit_then_blocks_and_recovers():
    limiter = FixedWindowLimiter(2, window_seconds=0.2)
    assert limiter.allow("k") is True
    assert limiter.allow("k") is True
    assert limiter.allow("k") is False
    time.sleep(0.25)
    assert limiter.allow("k") is True


def test_keys_are_isolated():
    limiter = FixedWindowLimiter(1)
    assert limiter.allow("a") is True
    assert limiter.allow("a") is False
    assert limiter.allow("b") is True
