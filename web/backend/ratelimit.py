"""进程内固定窗口限流（P4-B）。

L3-A 单实例部署够用；**多实例必须迁移到 Redis**（否则每个实例各算一份配额），
这一点写进 `docs/operations/production-readiness.md`；限流只防误用与撞库，
不是安全边界（真正的安全边界是 Argon2id + 会话校验 + 配额闸）。
"""
from __future__ import annotations

import threading
import time


class FixedWindowLimiter:
    """按 key 计数的固定窗口限流：`limit <= 0` 表示不限流。"""

    def __init__(self, limit: int, window_seconds: float = 60.0, max_keys: int = 10_000):
        self.limit = limit
        self.window_seconds = window_seconds
        self.max_keys = max_keys
        self._lock = threading.Lock()
        self._buckets: dict[str, tuple[float, int]] = {}

    def allow(self, key: str) -> bool:
        if self.limit <= 0:
            return True
        now = time.monotonic()
        with self._lock:
            start, count = self._buckets.get(key, (now, 0))
            if now - start >= self.window_seconds:
                start, count = now, 0
            count += 1
            self._buckets[key] = (start, count)
            if len(self._buckets) > self.max_keys:
                # 防内存膨胀：丢弃已过窗口的 key（简单粗暴但足够）
                self._buckets = {
                    item_key: value for item_key, value in self._buckets.items()
                    if now - value[0] < self.window_seconds
                }
            return count <= self.limit
