"""快照存储接口与运行期抓取上下文（需求 34 E1 / TBD-1、TBD-8）。

- ``SnapshotStore`` 协议：web 走 PG 两表（见 ``web/backend/snapshots.py``）；
  CLI/eval 无库时 ``store=None`` —— 抓取继续、正文进 finding，但**不落盘**并标记
  ``snapshot_status=disabled``（需求 34 §5 TBD-2 失败分类表的 `not_configured` 行）；
- **内容寻址**：``snapshot_id = sha256(extractor_version + "\\x00" + text)`` ——
  版本进哈希，保证「同文不同提取器版本」不共享 id（TBD-5 红线：
  offset 只对同一 ``extractor_version`` + 同一 ``snapshot_sha256`` 成立）；
- **运行期上下文**（contextvar）：store + run_id + 预算 + 抓取器；由执行入口绑定
  （web runner/worker / cli）。**未绑定 ⇒ researcher 不做抓取**（零行为变化，测试友好）。
"""
from __future__ import annotations

import hashlib
import logging
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Iterator, Optional, Protocol

from config import config
from research_engine.net.robots import RobotsCache
from research_engine.net.web_fetch import FetchBudget, WebFetcher

_log = logging.getLogger("deepresearch.snapshots")


class SnapshotStore(Protocol):
    """快照持久化协议（web=PG 两表；CLI 无库不绑定 store）。"""

    def save_snapshot(self, *, run_id: str, url: str, content_type: str,
                      source_bytes: int, text: str, text_truncated: bool,
                      extractor_version: str) -> str:
        """落盘一份快照并关联 run；返回内容寻址 id（sha256 hex）。"""
        ...  # pragma: no cover

    def load_snapshot(self, sha256: str) -> Optional[str]:
        """读快照正文（E3 窗口喂裁判用）；不存在/失败返回 None。"""
        ...  # pragma: no cover


def snapshot_id_for(text: str, extractor_version: str) -> str:
    """内容寻址 id（版本进哈希；见模块 docstring）。"""
    payload = (extractor_version + "\x00" + text).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


@dataclass
class FetchContext:
    """一个 run 的抓取上下文（预算 / 抓取器 / store 绑定）。"""

    store: Optional[SnapshotStore]
    run_id: Optional[str]
    budget: FetchBudget
    fetcher: WebFetcher
    #: E3：快照正文读缓存（sha256 → text|None；run 生命周期内有效）
    snapshot_cache: dict = field(default_factory=dict)

    @classmethod
    def create(cls, *, store: Optional[SnapshotStore] = None,
               run_id: Optional[str] = None) -> FetchContext:
        """按 ``config.web_snapshot`` 构造真实上下文（预算 / robots / 抓取器）。"""
        cfg = config.web_snapshot
        budget = FetchBudget(
            max_fetches=cfg.max_fetches_per_run,
            max_bytes=cfg.max_bytes_per_run,
            max_seconds=cfg.max_seconds_per_run,
        )
        robots = RobotsCache(
            user_agent=cfg.user_agent,
            approved_domains=set(cfg.approved_domains),
            timeout=cfg.connect_timeout_seconds,
        )
        fetcher = WebFetcher(
            budget=budget,
            robots=robots,
            max_retries=cfg.max_retries,
            connect_timeout=cfg.connect_timeout_seconds,
            read_timeout=cfg.read_timeout_seconds,
            max_response_bytes=cfg.max_response_bytes,
            max_text_bytes=cfg.max_text_bytes,
            per_domain_interval=cfg.per_domain_interval_seconds,
            max_domain_failures=cfg.max_domain_failures,
        )
        return cls(store=store, run_id=run_id, budget=budget, fetcher=fetcher)


_ctx: ContextVar[Optional[FetchContext]] = ContextVar("dr_fetch_context", default=None)


def current_fetch_context() -> Optional[FetchContext]:
    """当前运行作用域的抓取上下文（未绑定返回 None ⇒ 不做抓取）。"""
    return _ctx.get()


def push_fetch_context(ctx: FetchContext) -> object:
    """绑定抓取上下文（长生命周期执行器用；配合 :func:`pop_fetch_context` 复位）。"""
    return _ctx.set(ctx)


def pop_fetch_context(token: object) -> None:
    _ctx.reset(token)  # type: ignore[arg-type]


@contextmanager
def use_fetch_context(ctx: FetchContext) -> Iterator[None]:
    """绑定抓取上下文（短生命周期调用用，如 CLI 单次 run / 测试）。"""
    token = _ctx.set(ctx)
    try:
        yield
    finally:
        _ctx.reset(token)


def load_snapshot_text(snapshot_id: str) -> Optional[str]:
    """读快照正文（需求 34 E3 窗口喂裁判用）。

    - 无上下文 / 无 store（CLI）⇒ None（调用方回落头尾截断）；
    - per-run 缓存（``FetchContext.snapshot_cache``）：同一快照只读一次；
    - 读取失败静默降级（不写降级日志，N5 口径），缓存 None 防重复尝试。
    """
    ctx = _ctx.get()
    if ctx is None or ctx.store is None or not snapshot_id:
        return None
    if snapshot_id in ctx.snapshot_cache:
        return ctx.snapshot_cache[snapshot_id]
    text: Optional[str] = None
    try:
        text = ctx.store.load_snapshot(snapshot_id)
    except Exception as exc:  # noqa: BLE001 —— 读侧降级，不阻塞校验
        _log.warning("快照读取失败（降级）：%s", exc)
    ctx.snapshot_cache[snapshot_id] = text
    return text
