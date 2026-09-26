"""Redis 任务队列（P3-A）。

- 队列只存 `run_id`（LPUSH 入队 / BRPOP 领取），**不作为事实来源** ——
  权威状态在 PostgreSQL 的 `runs`（ADR-0009 原则 5）；
- 领取是「先出队、后原子认领」：`RunStore.claim_run` 用
  `WHERE status='QUEUED'` 兜底重复投递与多 Worker 竞争；
- 崩溃丢失的「已出队未认领」任务由 P3-B 的租约清扫兜底。
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
        """入队，返回入队后的队列长度。"""
        return int(self._client.lpush(self._key, run_id))

    def dequeue(self, timeout: float = 5.0) -> Optional[str]:
        """阻塞领取（最长 `timeout` 秒）；空队列返回 None。

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
