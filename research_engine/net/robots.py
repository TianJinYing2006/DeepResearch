"""robots.txt 检查（需求 34 E1 / TBD-2② 拍板：遵守，2026-10-11）。

拍板口径（需求 34 §5 TBD-2②）：
- **按域名检查**（每个 run 内缓存一次）；
- robots 拒绝 ⇒ 抓取层记 ``skipped_robots``（不写 ``degradation_log``）；
- 某目标**无法完成检查**（网络失败 / 5xx / 检查器限制）⇒ **仅抓「明确批准的来源」**
  （配置 ``DR_WEB_FETCH_APPROVED_DOMAINS`` 白名单），**默认拒绝**；
- ``robots.txt`` 404 ⇒ 视为允许（标准语义）；
- 3xx：safe_fetch 禁重定向 ⇒ 先按原 scheme 抓，3xx 时**换另一 scheme 同主机重试一次**
  （只限同一已校验主机，不跟随 Location，安全边界不变）。
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable, Optional
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

from research_engine.net.safe_fetch import FetchResult, safe_fetch


@dataclass(frozen=True)
class RobotsDecision:
    """一次 robots 判定。``detail`` 取值：allow / not_found_allow / disallow /
    approved（检查不可用但域名在白名单）/ unavailable_deny（检查不可用且不在白名单）。"""

    allowed: bool
    detail: str


class RobotsCache:
    """按域名缓存的 robots 检查器（线程安全；缓存生命周期 = 一个 run）。"""

    def __init__(
        self,
        *,
        user_agent: str,
        approved_domains: Optional[set] = None,
        timeout: float = 3.0,
        fetch: Callable[..., FetchResult] = safe_fetch,
    ) -> None:
        self._ua = user_agent
        self._approved = {d.lower() for d in (approved_domains or set())}
        self._timeout = timeout
        self._fetch = fetch
        self._cache: dict = {}
        self._lock = threading.Lock()

    def check(self, url: str) -> RobotsDecision:
        host = (urlsplit(url).hostname or "").lower()
        if not host:
            return RobotsDecision(False, "unavailable_deny")
        with self._lock:
            cached = self._cache.get(host)
        if cached is None:
            cached = self._load(host, url)
            with self._lock:
                self._cache[host] = cached
        kind, payload = cached
        if kind == "static":
            return payload
        # 命中缓存的解析器：按**具体 URL 路径**判定（allowed 与路径相关，不可按域名缓存布尔）
        parser: RobotFileParser = payload
        try:
            allowed = parser.can_fetch(self._ua, url)
        except Exception:  # noqa: BLE001
            return self._unavailable(host)
        return RobotsDecision(bool(allowed), "allow" if allowed else "disallow")

    def _load(self, host: str, url: str) -> tuple:
        """抓取并解析 robots.txt；返回 ("parser", RobotFileParser) 或 ("static", RobotsDecision)。"""
        parts = urlsplit(url)
        schemes = [parts.scheme or "https"]
        schemes.append("http" if schemes[0] == "https" else "https")
        result: Optional[FetchResult] = None
        for scheme in schemes:
            robots_url = f"{scheme}://{parts.netloc}/robots.txt"
            try:
                res = self._fetch(robots_url, timeout=self._timeout,
                                  connect_timeout=self._timeout)
            except Exception:  # noqa: BLE001 —— 任何抓取异常 ⇒ 检查不可用
                continue
            if 300 <= res.status < 400:
                continue  # 不跟随重定向：换 scheme 同主机再试一次
            result = res
            break
        if result is None:
            return ("static", self._unavailable(host))
        if result.status == 404:
            return ("static", RobotsDecision(True, "not_found_allow"))
        if not (200 <= result.status < 300):
            return ("static", self._unavailable(host))
        parser = RobotFileParser()
        try:
            parser.parse(result.text.splitlines())
        except Exception:  # noqa: BLE001 —— 解析异常视为检查不可用（保守）
            return ("static", self._unavailable(host))
        return ("parser", parser)

    def _unavailable(self, host: str) -> RobotsDecision:
        if host in self._approved:
            return RobotsDecision(True, "approved")
        return RobotsDecision(False, "unavailable_deny")
