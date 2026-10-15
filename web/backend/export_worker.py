"""需求 38 F2：PDF 导出服务（supervisor）——PG 认领 / 租约 / 超时 / 结果发布。

架构（需求 38 §12.4 方案 B + 2026-10-16 容量实测修订）：
- **PG `export_jobs` 为权威状态**：`FOR UPDATE SKIP LOCKED` 认领；60s 租约；过期回收重排
  （attempt ≤ max_attempts）；结果元数据（key/bytes/hash/expires）在 PG；
- **Redis 分工**：主 Redis 仅作**唤醒信号**（丢失无害，2s 轮询兜底）；结果 bytes 写
  **专用非持久化 Redis**（`DR_EXPORT_REDIS_URL`；noeviction）——总字节预算内，写满 ⇒
  明确失败（`export_cache_full`），**不驱逐**未过期结果、**不退回** API 同步渲染；
- **渲染在常驻单个渲染子进程内**（`spawn`；保持字体/引擎热态，避免每作业 +260MB 冷启动；
  并发 1）：超时/异常由 supervisor 硬杀并重建子进程，按有限重试处理；
- **内存回收**：容器 `memory.current` 接近阈值（默认 600MB）或每处理 N 个作业（默认 25）
  主动回收渲染子进程；阈值看容器总量，不只看子进程 RSS。

限额（v1 初始护栏；上线后按实际分布调整——需求 38 §12.7）：
输入 ≤ 2MiB / 页数 ≤ 100 / 输出 ≤ 20MiB / 渲染硬超时 30s / 结果 TTL 15min /
结果总预算 192MiB（专用 Redis maxmemory 256MB，noeviction）。
"""
from __future__ import annotations

import dataclasses
import json
import logging
import multiprocessing
import os
import signal
import socket
import time
from datetime import UTC, datetime, timedelta
from typing import Any, Dict, Optional

import redis as redis_lib

from web.backend import pdf_render
from web.backend.export import document_from_store
from web.backend.objectstore import get_object_store
from web.backend.store import RunStore

_log = logging.getLogger("deepresearch.export")


def _env_int(name: str, default: int) -> int:
    try:
        return int((os.getenv(name) or "").strip() or default)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float((os.getenv(name) or "").strip() or default)
    except ValueError:
        return default


MAX_INPUT_BYTES = _env_int("DR_EXPORT_PDF_MAX_INPUT_BYTES", 2 * 1024 * 1024)
MAX_PAGES = _env_int("DR_EXPORT_PDF_MAX_PAGES", 100)
MAX_OUTPUT_BYTES = _env_int("DR_EXPORT_PDF_MAX_OUTPUT_BYTES", 20 * 1024 * 1024)
RENDER_TIMEOUT = _env_float("DR_EXPORT_PDF_TIMEOUT_SECONDS", 30.0)
RESULT_TTL_SECONDS = _env_int("DR_EXPORT_RESULT_TTL_SECONDS", 900)
RESULT_BUDGET_BYTES = _env_int("DR_EXPORT_RESULT_BUDGET_BYTES", 192 * 1024 * 1024)
RECYCLE_EVERY_JOBS = _env_int("DR_EXPORT_RECYCLE_EVERY_JOBS", 25)
RECYCLE_MEMORY_MB = _env_int("DR_EXPORT_RECYCLE_MEMORY_MB", 600)
LEASE_SECONDS = 60
POLL_TIMEOUT_SECONDS = 2
WAKE_KEY = "dr:export:wake"
RESULT_KEY_PREFIX = "dr:export:result:"

#: 确定性失败（重试无意义）⇒ 直接 FAILED；其余（超时/渲染异常）按有限重试处理
_DETERMINISTIC_CODES = frozenset({
    "export_too_large", "export_too_many_pages", "export_report_unavailable",
    "export_cache_full",
})


class RenderTimeoutError(RuntimeError):
    pass


def _read_artifact_text(store: RunStore, run_id: str, kind: str) -> Optional[str]:
    """读文本产物（PG/对象存储双路径；失败/缺失返回 None，不抛）。"""
    try:
        artifact = store.get_artifact_row(run_id, kind)
    except Exception:  # noqa: BLE001
        return None
    if not artifact:
        return None
    body = artifact.get("body") or ""
    if artifact.get("storage") == "s3":
        object_store = get_object_store()
        if object_store is None:
            return None
        try:
            body = object_store.get_text(artifact.get("object_key")) or ""
        except Exception:  # noqa: BLE001
            return None
    return body


def _container_memory_mb() -> float:
    """容器当前内存（cgroup v2/v1；读不到返回 0 = 不触发回收）。"""
    for path in ("/sys/fs/cgroup/memory.current", "/sys/fs/cgroup/memory/memory.usage_in_bytes"):
        try:
            with open(path, encoding="ascii") as fh:
                return int(fh.read().strip()) / (1024 * 1024)
        except (OSError, ValueError):
            continue
    return 0.0


def build_render_input(store: RunStore, job: Dict[str, Any]) -> Dict[str, Any]:
    """装配渲染输入（ExportDocument dict）；报告缺失 ⇒ 结构化错误。"""
    run_id = str(job["run_id"])
    row = store.get_run(run_id)
    if row is None:
        raise pdf_render.PdfRenderError("export_report_unavailable", "run 不存在")
    doc = document_from_store(
        row,
        export_json=_read_artifact_text(store, run_id, "export_json"),
        report_md=_read_artifact_text(store, run_id, "report_md"),
    )
    if not doc.report.strip():
        raise pdf_render.PdfRenderError("export_report_unavailable", "本次运行没有产出报告")
    doc_dict = dataclasses.asdict(doc)
    input_bytes = len(json.dumps(doc_dict, ensure_ascii=False).encode("utf-8"))
    if input_bytes > MAX_INPUT_BYTES:
        raise pdf_render.PdfRenderError(
            "export_too_large", f"input_bytes={input_bytes} limit={MAX_INPUT_BYTES}")
    return doc_dict


class ExportSupervisor:
    """单并发 supervisor：认领 → 渲染（常驻子进程）→ 发布结果；超时硬杀重建。"""

    def __init__(self, store: RunStore, result_redis: Any,
                 wake_redis: Optional[Any] = None, *, owner: Optional[str] = None,
                 ctx: Optional[Any] = None) -> None:
        self.store = store
        self.result_redis = result_redis
        self.wake_redis = wake_redis
        self.owner = owner or f"export-{socket.gethostname()}-{os.getpid()}"
        self._ctx = ctx or multiprocessing.get_context("spawn")
        self._parent_conn: Optional[Any] = None
        self._child: Optional[Any] = None
        self._jobs_since_recycle = 0

    # ---- 渲染子进程生命周期 ----

    def _ensure_child(self) -> None:
        if self._child is not None and self._child.is_alive():
            return
        self._kill_child()
        parent_conn, child_conn = self._ctx.Pipe()
        self._child = self._ctx.Process(
            target=pdf_render.child_main, args=(child_conn,), daemon=True,
            name="pdf-render-child")
        self._child.start()
        child_conn.close()
        self._parent_conn = parent_conn

    def _kill_child(self) -> None:
        if self._child is not None:
            try:
                if self._child.is_alive():
                    self._child.terminate()
                    self._child.join(timeout=5)
                if self._child.is_alive():
                    self._child.kill()
                    self._child.join(timeout=5)
            except Exception:  # noqa: BLE001
                pass
        if self._parent_conn is not None:
            try:
                self._parent_conn.close()
            except Exception:  # noqa: BLE001
                pass
        self._child = None
        self._parent_conn = None

    def _recycle_if_needed(self) -> None:
        self._jobs_since_recycle += 1
        if self._jobs_since_recycle >= RECYCLE_EVERY_JOBS:
            _log.info("渲染子进程周期性回收（%d 个作业）", self._jobs_since_recycle)
            self._kill_child()
            self._jobs_since_recycle = 0
            return
        mem = _container_memory_mb()
        if mem >= RECYCLE_MEMORY_MB:
            _log.info("容器内存 %.1fMB ≥ %dMB，回收渲染子进程", mem, RECYCLE_MEMORY_MB)
            self._kill_child()
            self._jobs_since_recycle = 0

    def render(self, doc_dict: Dict[str, Any]) -> Dict[str, Any]:
        """把渲染请求发给常驻子进程并等待（硬超时 RENDER_TIMEOUT）；超时 ⇒ 硬杀重建。"""
        self._ensure_child()
        assert self._parent_conn is not None
        try:
            self._parent_conn.send({
                "doc": doc_dict, "max_pages": MAX_PAGES,
                "max_output_bytes": MAX_OUTPUT_BYTES,
            })
        except Exception as exc:  # noqa: BLE001 —— 管道断裂：重建后按瞬时失败处理
            self._kill_child()
            raise RenderTimeoutError(f"render pipe broken: {type(exc).__name__}") from exc
        if not self._parent_conn.poll(RENDER_TIMEOUT):
            self._kill_child()  # 硬超时：终止并重建（内存随进程释放）
            raise RenderTimeoutError(f"render timeout > {RENDER_TIMEOUT}s")
        return self._parent_conn.recv()

    # ---- 结果发布 ----

    def publish_result(self, job_id: str, pdf: bytes) -> None:
        """写专用结果 Redis（预算内；写满 ⇒ export_cache_full，不驱逐不退回同步渲染）。"""
        try:
            used = int(self.result_redis.info("memory").get("used_memory") or 0)
        except Exception:  # noqa: BLE001 —— 读不到用量时按预算足额判断（宁缺勿错）
            used = 0
        if used + len(pdf) > RESULT_BUDGET_BYTES:
            raise pdf_render.PdfRenderError(
                "export_cache_full", f"used={used} need={len(pdf)} budget={RESULT_BUDGET_BYTES}")
        self.result_redis.set(RESULT_KEY_PREFIX + job_id, pdf, ex=RESULT_TTL_SECONDS)

    # ---- 单作业处理 ----

    def process_job(self, job: Dict[str, Any]) -> None:
        job_id = str(job["job_id"])
        try:
            doc_dict = build_render_input(self.store, job)
            reply = self.render(doc_dict)
        except pdf_render.PdfRenderError as exc:
            self._finish_failure(job_id, exc.code, str(exc))
            return
        except RenderTimeoutError as exc:
            self._requeue_or_fail(job_id, "export_render_timeout", str(exc))
            return
        if not reply.get("ok"):
            self._requeue_or_fail(job_id, str(reply.get("error_code") or "export_render_failed"),
                                  str(reply.get("detail") or ""))
            return
        pdf = reply["pdf"]
        try:
            self.publish_result(job_id, pdf)
        except pdf_render.PdfRenderError as exc:
            self._finish_failure(job_id, exc.code, str(exc))
            return
        expires_at = datetime.now(UTC) + timedelta(seconds=RESULT_TTL_SECONDS)
        ok = self.store.finish_export_job_success(
            job_id, self.owner, result_key=RESULT_KEY_PREFIX + job_id,
            result_bytes=len(pdf), result_hash=str(reply.get("sha256") or ""),
            result_expires_at=expires_at)
        if not ok:
            _log.warning("作业 %s 完成时租约已失效（结果已写缓存，PG 未确认）", job_id)

    def _finish_failure(self, job_id: str, code: str, detail: str) -> None:
        self.store.finish_export_job_failure(
            job_id, self.owner, error_code=code, error_detail=detail)

    def _requeue_or_fail(self, job_id: str, code: str, detail: str) -> None:
        if code in _DETERMINISTIC_CODES:
            self._finish_failure(job_id, code, detail)
            return
        new_status = self.store.requeue_export_job(
            job_id, self.owner, error_code=code, error_detail=detail)
        _log.info("作业 %s 瞬时失败（%s）⇒ %s", job_id, code, new_status)

    # ---- 主循环 ----

    def _wait_for_wake(self) -> None:
        if self.wake_redis is None:
            time.sleep(POLL_TIMEOUT_SECONDS)
            return
        try:
            self.wake_redis.blpop(WAKE_KEY, timeout=POLL_TIMEOUT_SECONDS)
        except Exception:  # noqa: BLE001 —— 唤醒通道故障退化为轮询
            time.sleep(POLL_TIMEOUT_SECONDS)

    def run(self) -> None:
        _log.info("export supervisor 启动：owner=%s", self.owner)
        while True:
            try:
                self.store.reclaim_expired_export_leases()
                self.store.expire_export_jobs()
                job = self.store.claim_export_job(self.owner, lease_seconds=LEASE_SECONDS)
                if job is None:
                    self._wait_for_wake()
                    continue
                self.process_job(job)
                self._recycle_if_needed()
            except KeyboardInterrupt:
                raise
            except Exception as exc:  # noqa: BLE001 —— 循环级故障不退出（含 DB 抖动）
                _log.error("export supervisor 循环异常：%s: %s", type(exc).__name__, exc)
                time.sleep(POLL_TIMEOUT_SECONDS)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    dsn = (os.getenv("DR_DATABASE_URL") or "").strip()
    if not dsn:
        raise SystemExit("DR_DATABASE_URL 未配置（export 服务需要任务库）")
    result_url = (os.getenv("DR_EXPORT_REDIS_URL") or "").strip()
    if not result_url:
        raise SystemExit("DR_EXPORT_REDIS_URL 未配置（结果通道必须为专用非持久化 Redis）")
    wake_url = (os.getenv("DR_REDIS_URL") or "").strip()
    wake_redis = redis_lib.Redis.from_url(wake_url) if wake_url else None
    result_redis = redis_lib.Redis.from_url(result_url)
    supervisor = ExportSupervisor(RunStore(dsn), result_redis, wake_redis)
    signal.signal(signal.SIGTERM, lambda *_: raise_system_exit())  # noqa: ARG005
    supervisor.run()


def raise_system_exit() -> None:
    raise SystemExit(0)


if __name__ == "__main__":
    main()
