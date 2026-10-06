"""进程内运行指标（P8-A）。

只做**最小可判定**：HTTP 状态分类计数 / 延迟累计、SSE 连接数、启动时间。
任务侧指标（状态分布 / 积压 / 成本）来自任务库实时查询，不在这里重复记账。

诚实边界：单实例内存计数，进程重启即清零；多实例部署前必须迁移到 Prometheus /
中心化指标（或由反向代理层统计），这一点与限流同源（见上线清单）。
"""
from __future__ import annotations

import threading
import time
from typing import Dict

_started_at = time.time()


class Metrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: Dict[str, int] = {}
        self._latency_count = 0
        self._latency_sum_ms = 0.0
        self._sse_connections = 0

    def inc(self, key: str, amount: int = 1) -> None:
        with self._lock:
            self._counters[key] = self._counters.get(key, 0) + amount

    def observe_latency_ms(self, value: float) -> None:
        with self._lock:
            self._latency_count += 1
            self._latency_sum_ms += value

    def sse_open(self) -> None:
        with self._lock:
            self._sse_connections += 1

    def sse_close(self) -> None:
        with self._lock:
            self._sse_connections = max(0, self._sse_connections - 1)

    def snapshot(self) -> dict:
        with self._lock:
            counters = dict(self._counters)
            latency_avg = (
                round(self._latency_sum_ms / self._latency_count, 1)
                if self._latency_count else 0.0
            )
            sse = self._sse_connections
        return {
            "uptime_seconds": round(time.time() - _started_at, 1),
            "http": {
                "2xx": counters.get("http_2xx", 0),
                "4xx": counters.get("http_4xx", 0),
                "5xx": counters.get("http_5xx", 0),
                "latency_ms_avg": latency_avg,
            },
            "sse_connections": sse,
        }


METRICS = Metrics()
