"""网页抓取策略层（需求 34 E1 / TBD-2 拍板口径，2026-10-11）。

执行序（每个候选 URL）：
`预算闸 → robots 检查 → 域名熔断 → 同域名串行 + 间隔 → safe_fetch（重试 ≤2）→ 正文提取`

拍板口径（需求 34 §5 TBD-2）：
- **预算三闸**（run 级）：次数 ≤15 / 原始字节 ≤5MB / 抓取耗时 ≤90s；
  每次**尝试**都计数（含重试；「自动重试 ≤2 次，计入抓取额度」）；
- **重试**：≤2 次，仅对 timeout / 连接类 provider_error（4xx/5xx/redirect/parse 不重试）；
- **限速**：同域名串行 + ≥200ms 间隔；**熔断**：同域名连续失败 2 次 ⇒ 本 run 不再抓；
- **失败分类复用既有工具层 5 值**（`FailureReason`），不新增枚举（TBD-7 红线②）；
- 抓取失败**不写 `degradation_log`**（N5 / D-03）；每次抓取发 OTel span（N3）。
"""
from __future__ import annotations

import logging
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from dataclasses import dataclass, field
from typing import Callable, List, Optional
from urllib.parse import urlsplit

from research_engine.failure_reasons import FailureReason
from research_engine.net.robots import RobotsCache
from research_engine.net.safe_fetch import (
    DEFAULT_MAX_BYTES,
    FetchResult,
    UnsafeUrlError,
    safe_fetch,
)
from research_engine.net.webtext import ExtractedText, extract_text, looks_like_pdf
from research_engine.observability import span_node

_log = logging.getLogger("deepresearch.web_fetch")

# ---- 快照状态词表（TBD-2/TBD-3 拍板口径；写入 finding.metadata["snapshot_status"]）----
STATUS_OK = "ok"                        # 抓取 + 提取成功
STATUS_MISSING = "missing"              # 抓取/提取失败（timeout / 4xx5xx / parse / 超限 / 熔断）
STATUS_SKIPPED_BUDGET = "skipped_budget"    # run 预算耗尽
STATUS_SKIPPED_ROBOTS = "skipped_robots"    # robots 拒绝或检查不可用且未获批准
STATUS_SKIPPED_PDF = "skipped_pdf"      # PDF 不抓（登记计数）
STATUS_UNSUPPORTED = "unsupported"      # 无直接可读正文（如 JS 渲染页；标记「暂不支持」）
STATUS_DISABLED = "disabled"            # 抓到但未落盘（无 store；TBD-2 not_configured 行）
STATUS_NOT_ATTEMPTED = "not_attempted"  # top-N 之外未尝试抓取的候选


@dataclass
class FetchBudget:
    """run 级抓取预算三闸（线程安全）。"""

    max_fetches: int
    max_bytes: int
    max_seconds: float
    fetches: int = 0
    bytes: int = 0
    seconds: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def try_acquire(self) -> bool:
        """预扣一次抓取额度；越闸返回 False（本次不抓）。"""
        with self._lock:
            if self.fetches + 1 > self.max_fetches:
                return False
            if self.bytes >= self.max_bytes:
                return False
            if self.seconds >= self.max_seconds:
                return False
            self.fetches += 1
            return True

    def record(self, nbytes: int, seconds: float) -> None:
        with self._lock:
            self.bytes += max(0, int(nbytes))
            self.seconds += max(0.0, float(seconds))

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "fetches": self.fetches,
                "bytes": self.bytes,
                "seconds": round(self.seconds, 3),
                "max_fetches": self.max_fetches,
                "max_bytes": self.max_bytes,
                "max_seconds": self.max_seconds,
            }


@dataclass(frozen=True)
class FetchOutcome:
    """一次抓取（含重试）的最终结果。"""

    url: str
    status: str
    reason: str = ""            # 失败时为 FailureReason 值（工具层 5 值）
    detail: str = ""
    content_type: str = ""
    source_bytes: int = 0
    attempts: int = 1
    extracted: Optional[ExtractedText] = None

    @property
    def ok(self) -> bool:
        return self.status == STATUS_OK


class WebFetcher:
    """带策略的网页抓取器（一个 run 一个实例；状态：预算 / robots 缓存 / 熔断 / 限速）。"""

    def __init__(
        self,
        *,
        budget: FetchBudget,
        robots: RobotsCache,
        max_retries: int = 2,
        connect_timeout: float = 3.0,
        read_timeout: float = 10.0,
        max_response_bytes: int = DEFAULT_MAX_BYTES,
        max_text_bytes: int = 64 * 1024,
        per_domain_interval: float = 0.2,
        max_domain_failures: int = 2,
        max_concurrency: int = 4,
        fetch: Callable[..., FetchResult] = safe_fetch,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._budget = budget
        self._robots = robots
        self._max_retries = max(0, int(max_retries))
        self._connect_timeout = connect_timeout
        self._read_timeout = read_timeout
        self._max_response_bytes = max_response_bytes
        self._max_text_bytes = max_text_bytes
        self._per_domain_interval = max(0.0, per_domain_interval)
        self._max_domain_failures = max(1, int(max_domain_failures))
        self._max_concurrency = max(1, int(max_concurrency))
        self._fetch = fetch
        self._clock = clock
        self._sleep = sleep
        self._domain_state: dict = {}
        self._state_lock = threading.Lock()

    # ---- 批量（top-N 候选；并发 ≤4，同域名仍串行）----

    def fetch_many(self, urls: List[str]) -> List[FetchOutcome]:
        targets = [u for u in urls if u]
        if not targets:
            return []
        workers = max(1, min(len(targets), self._max_concurrency))
        with ThreadPoolExecutor(max_workers=workers) as ex:
            # 工具线程不继承父线程 ContextVar（同 researcher F05 口径）⇒ 逐任务 copy_context
            futs = [ex.submit(copy_context().run, self.fetch, u) for u in targets]
            return [fut.result() for fut in futs]

    # ---- 单 URL ----

    def fetch(self, url: str) -> FetchOutcome:
        domain = self._domain_of(url)
        with span_node("抓取网页", node="web_fetch", url=url, domain=domain) as sp:
            outcome = self._fetch_guarded(url, domain)
            if sp is not None:
                try:
                    sp.set_attribute("dr.status", outcome.status)
                    sp.set_attribute("dr.attempts", outcome.attempts)
                    sp.set_attribute("dr.source_bytes", outcome.source_bytes)
                    if outcome.reason:
                        sp.set_attribute("dr.reason", outcome.reason)
                    if outcome.detail:
                        sp.set_attribute("dr.detail", outcome.detail[:120])
                except Exception:  # noqa: BLE001 —— 观测旁路绝不拖累主链路
                    pass
            return outcome

    def _fetch_guarded(self, url: str, domain: str) -> FetchOutcome:
        if not self._budget.try_acquire():
            return FetchOutcome(url, STATUS_SKIPPED_BUDGET, detail="run budget exhausted")
        decision = self._robots.check(url)
        if not decision.allowed:
            return FetchOutcome(url, STATUS_SKIPPED_ROBOTS, detail=decision.detail)
        state = self._state_for(domain)
        with state["lock"]:  # 同域名串行
            self._respect_interval(state)
            if state["failures"] >= self._max_domain_failures:
                return FetchOutcome(
                    url, STATUS_MISSING, reason=FailureReason.PROVIDER_ERROR.value,
                    detail="circuit_open")
            outcome = self._attempt_with_retries(url)
            state["last"] = self._clock()
            if outcome.status in (STATUS_OK, STATUS_SKIPPED_PDF, STATUS_UNSUPPORTED):
                state["failures"] = 0
            elif outcome.status == STATUS_MISSING:
                state["failures"] += 1
            return outcome

    # ---- 内部 ----

    @staticmethod
    def _domain_of(url: str) -> str:
        return (urlsplit(url).hostname or "").lower()

    def _state_for(self, domain: str) -> dict:
        with self._state_lock:
            state = self._domain_state.get(domain)
            if state is None:
                state = {"lock": threading.Lock(), "last": None, "failures": 0}
                self._domain_state[domain] = state
            return state

    def _respect_interval(self, state: dict) -> None:
        last = state.get("last")
        if last is None:
            return
        wait = self._per_domain_interval - (self._clock() - last)
        if wait > 0:
            self._sleep(wait)

    def _attempt_with_retries(self, url: str) -> FetchOutcome:
        """1 次初始尝试 + 重试 ≤max_retries；**每次尝试都预扣一次抓取额度**（TBD-2 拍板）。"""
        outcome = FetchOutcome(url, STATUS_MISSING, reason=FailureReason.PROVIDER_ERROR.value)
        for attempt in range(1, self._max_retries + 2):
            if attempt > 1 and not self._budget.try_acquire():
                break  # 重试额度耗尽：返回最后一次失败结果
            outcome = self._single_attempt(url, attempt)
            if not self._retryable(outcome):
                return outcome
        return outcome

    def _retryable(self, outcome: FetchOutcome) -> bool:
        """仅 timeout / 连接类错误重试（「自动重试 ≤2 次，计入抓取额度」）。"""
        if outcome.status != STATUS_MISSING:
            return False
        return (outcome.reason == FailureReason.TIMEOUT.value
                or outcome.detail.startswith("connect:"))

    def _single_attempt(self, url: str, attempt: int) -> FetchOutcome:
        started = self._clock()
        try:
            res = self._fetch(
                url,
                timeout=self._read_timeout,
                connect_timeout=self._connect_timeout,
                max_bytes=self._max_response_bytes,
            )
        except UnsafeUrlError as exc:
            self._budget.record(0, self._clock() - started)
            return self._classify_unsafe(url, str(exc), attempt)
        except Exception as exc:  # noqa: BLE001 —— urllib3 / 网络层异常
            self._budget.record(0, self._clock() - started)
            reason = self._classify_exception(exc)
            return FetchOutcome(
                url, STATUS_MISSING, reason=reason,
                detail=f"connect:{type(exc).__name__}", attempts=attempt)
        elapsed = self._clock() - started
        self._budget.record(len(res.body), elapsed)
        source_bytes = len(res.body)
        if res.status >= 400:
            return FetchOutcome(url, STATUS_MISSING, reason=FailureReason.PROVIDER_ERROR.value,
                                detail=f"http_{res.status}", content_type=res.content_type,
                                source_bytes=source_bytes, attempts=attempt)
        if looks_like_pdf(res.content_type, url):
            return FetchOutcome(url, STATUS_SKIPPED_PDF, content_type=res.content_type,
                                source_bytes=source_bytes, attempts=attempt)
        extracted = extract_text(res.body, res.content_type, url, max_bytes=self._max_text_bytes)
        if extracted.kind == "pdf":
            return FetchOutcome(url, STATUS_SKIPPED_PDF, content_type=res.content_type,
                                source_bytes=source_bytes, attempts=attempt)
        if extracted.kind == "unsupported":
            return FetchOutcome(url, STATUS_MISSING, reason=FailureReason.PARSE_ERROR.value,
                                detail="non_text", content_type=res.content_type,
                                source_bytes=source_bytes, attempts=attempt)
        if extracted.kind == "empty":
            return FetchOutcome(url, STATUS_UNSUPPORTED, detail="no_readable_text",
                                content_type=res.content_type,
                                source_bytes=source_bytes, attempts=attempt)
        return FetchOutcome(url, STATUS_OK, content_type=res.content_type,
                            source_bytes=source_bytes, attempts=attempt, extracted=extracted)

    @staticmethod
    def _classify_unsafe(url: str, message: str, attempt: int) -> FetchOutcome:
        if "redirects" in message:
            return FetchOutcome(url, STATUS_MISSING, reason=FailureReason.PROVIDER_ERROR.value,
                                detail="redirect", attempts=attempt)
        if "exceeds" in message:
            return FetchOutcome(url, STATUS_MISSING, reason=FailureReason.PARSE_ERROR.value,
                                detail="too_large", attempts=attempt)
        return FetchOutcome(url, STATUS_MISSING, reason=FailureReason.PROVIDER_ERROR.value,
                            detail=f"unsafe:{message[:80]}", attempts=attempt)

    @staticmethod
    def _classify_exception(exc: Exception) -> str:
        if isinstance(exc, (socket.timeout, TimeoutError)):
            return FailureReason.TIMEOUT.value
        name = type(exc).__name__.lower()
        text = str(exc).lower()
        if "timeout" in name or "timed out" in text:
            return FailureReason.TIMEOUT.value
        return FailureReason.PROVIDER_ERROR.value
