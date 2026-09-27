"""Redis 唤醒信号（P0-2 起降级；派发权威已迁到 PostgreSQL）。

- 权威派发：`RunStore.claim_next_queued`（`FOR UPDATE SKIP LOCKED`）——不存在
  「先出队、后认领」的丢失窗口；Worker 每轮直接查库领取；
- 本模块的 `enqueue` / `dequeue` 只是**唤醒信号**（降低领取延迟）：
  丢失、重复、Redis 整体不可用都不影响正确性（退化为按 `poll_seconds` 轮询）；
- 队列深度请用 `RunStore.count_queued`（`/api/health`、`/api/metrics` 已切换）。

保留 Redis 客户端是为后续限流 / 缓存 / Streams 演进留位，不再承担任务投递。
"""
from __future__ import annotations

from typing import Optional

import redis
from redis.exceptions import TimeoutError as RedisTimeoutError

QUEUE_KEY = "dr:runs:queue"


class RunQueue:
    def __init__(self, url: str, key: str = QUEUE_KEY):
        self._client = redis.Redis.from_url(url, decode_responses=True)
        self._key = key

    def enqueue(self, run_id: str) -> int:
        """发一条唤醒信号（可丢），返回信号队列长度。"""
        return int(self._client.lpush(self._key, run_id))

    def dequeue(self, timeout: float = 5.0) -> Optional[str]:
        """阻塞等待唤醒信号（最长 `timeout` 秒）；空队列返回 None。

        ⚠️ redis-py 8 的阻塞命令把客户端读超时设成与阻塞超时同值，空队列时往往先抛
        `TimeoutError`（实测复现）—— 语义上就是「没有新任务」，按 None 处理；
        真正的连接故障是 `ConnectionError`，不在此列，会向上抛出。
        """
        try:
            item = self._client.brpop(self._key, timeout=max(1, int(timeout)))
        except RedisTimeoutError:
            return None
        return item[1] if item else None

    def depth(self) -> int:
        return int(self._client.llen(self._key))

    def ping(self) -> bool:
        return bool(self._client.ping())
