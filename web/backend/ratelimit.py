"""限流（P4-B 进程内固定窗口 → P1-2 Redis 滑动窗口）。

- `FixedWindowLimiter`：进程内固定窗口，**兜底**（未配置 Redis / 本地开发）；
- `RedisSlidingWindowLimiter`：Redis + Lua 原子滑动窗口计数（业界通用默认，
  近精确且内存 O(1)）；多 API 实例共享同一计数；
- 失败策略（明确声明）：**fail-open** —— Redis 抖动时放行并记
  `ratelimit_redis_error` 指标；限流是防误用/撞库的纵深，不是唯一安全边界
  （真正的边界是 Argon2id + 会话校验 + 配额闸）。
"""
from __future__ import annotations

import threading
import time
from typing import Optional

import redis

from .metrics import METRICS

#: 滑动窗口计数 Lua：KEYS=[当前窗口, 上一窗口]，ARGV=[窗口内已过秒数, 窗口秒数, 上限]
_SLIDING_WINDOW_LUA = """
local elapsed = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local current = tonumber(redis.call('GET', KEYS[1]) or '0')
local previous = tonumber(redis.call('GET', KEYS[2]) or '0')
local weight = 1 - (elapsed / window)
local estimate = previous * weight + current
if estimate >= limit then
    return 0
end
local count = redis.call('INCR', KEYS[1])
if count == 1 then
    redis.call('EXPIRE', KEYS[1], window * 2)
end
return 1
"""


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


class RedisSlidingWindowLimiter:
    """Redis 滑动窗口计数限流（多实例共享；Lua 原子；fail-open）。

    `key_prefix` 内应包含 `{...}` 哈希标签，保证 Cluster 下两个窗口键同槽。
    """

    def __init__(self, url: str, limit: int, *, window_seconds: int = 60,
                 key_prefix: str = "{dr:rl}"):
        self.limit = limit
        self.window_seconds = window_seconds
        self._prefix = key_prefix
        self._client = redis.Redis.from_url(url, decode_responses=True)
        self._script = self._client.register_script(_SLIDING_WINDOW_LUA)

    def allow(self, key: str) -> bool:
        if self.limit <= 0:
            return True
        now = int(time.time())
        window_number = now // self.window_seconds
        base = f"{self._prefix}:{key}"
        keys = [f"{base}:{window_number}", f"{base}:{window_number - 1}"]
        try:
            result = self._script(
                keys=keys,
                args=[now % self.window_seconds, self.window_seconds, self.limit],
                client=self._client,
            )
        except Exception:  # noqa: BLE001 —— fail-open：Redis 抖动不阻断业务
            METRICS.inc("ratelimit_redis_error")
            return True
        return int(result) == 1

    def ping(self) -> bool:
        return bool(self._client.ping())


def make_limiter(name: str, limit: int, *, redis_url: Optional[str] = None,
                 window_seconds: int = 60) -> object:
    """按部署形态选择限流器：配置 Redis ⇒ 滑动窗口；否则进程内固定窗口。"""
    if limit <= 0:
        return FixedWindowLimiter(0)
    if redis_url:
        try:
            return RedisSlidingWindowLimiter(
                redis_url, limit, window_seconds=window_seconds,
                key_prefix=f"{{dr:rl:{name}}}")
        except Exception:  # noqa: BLE001 —— 构造失败回落进程内
            pass
    return FixedWindowLimiter(limit)
