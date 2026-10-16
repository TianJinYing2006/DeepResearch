"""DR_DEMO=1：PDF 导出作业的**内存实现**（e2e / 界面演示用；不连 PG、不调 WeasyPrint）。

目的：让 e2e 以真实客户端路径（POST 创建 → 轮询状态 → 授权下载）覆盖前端异步流程，
同时保持「零数据库、零渲染依赖」的演示纪律。语义与真实链路对齐的最小集：

- 创建即 `SUCCEEDED`（e2e 验证的是客户端状态机；真实渲染在 export 服务与 CI 渲染测试覆盖）；
- 幂等复用：进行中作业或未过期结果直接复用（同 `find_reusable_export_job` 口径）；
- 结果 bytes 为**静态最小 PDF**（内容不参与断言；下载名/字节由客户端核验）；
- `get_result` 只在真实模式的结果 Redis 缺失时被端点回退调用。
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Dict, Optional

#: 静态最小单页 PDF（合法 xref/trailer；e2e 只断言下载事件与文件名，不解析内容）
_TINY_PDF = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
    b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]>>endobj\n"
    b"trailer<</Size 4/Root 1 0 R>>\n%%EOF\n"
)

RESULT_TTL_SECONDS = 900


class DemoExportStore:
    """导出端点所需 store 子集的内存替身（仅 DR_DEMO=1 时挂载）。"""

    def __init__(self) -> None:
        self._jobs: Dict[str, Dict[str, Any]] = {}

    # ---- 端点用到的 store 接口 ----

    def get_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        """演示运行在内存 manager 中；这里只提供导出端点读取的终局字段。

        （按钮在 UI 侧由 hasReport 门控；到达此处的请求都可视为终局。）
        """
        return {
            "run_id": run_id,
            "topic": None,
            "finished_at": datetime.now(UTC),
            "moderation_status": None,
        }

    def find_reusable_export_job(self, request_hash: str) -> Optional[Dict[str, Any]]:
        now = datetime.now(UTC)
        for job in self._jobs.values():
            if job["request_hash"] != request_hash:
                continue
            if job["status"] in ("QUEUED", "RUNNING"):
                return job
            if job["status"] == "SUCCEEDED" and job["result_expires_at"] > now:
                return job
        return None

    def create_export_job(self, job_id: str, *, run_id: str, user_id: Optional[str],
                          request_hash: str, max_attempts: int = 2) -> Dict[str, Any]:
        job: Dict[str, Any] = {
            "job_id": job_id or uuid.uuid4().hex[:12],
            "run_id": run_id,
            "user_id": user_id,
            "kind": "pdf",
            "status": "SUCCEEDED",
            "attempt": 1,
            "max_attempts": max_attempts,
            "request_hash": request_hash,
            "lease_owner": None,
            "lease_expires_at": None,
            "error_code": None,
            "error_detail": None,
            "result_key": "dr:export:result:" + (job_id or ""),
            "result_bytes": len(_TINY_PDF),
            "result_hash": None,
            "result_expires_at": datetime.now(UTC) + timedelta(seconds=RESULT_TTL_SECONDS),
            "created_at": datetime.now(UTC),
            "started_at": datetime.now(UTC),
            "finished_at": datetime.now(UTC),
        }
        self._jobs[job["job_id"]] = job
        return job

    def get_export_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        return self._jobs.get(job_id)

    # ---- 结果回退（真实模式为专用结果 Redis；演示模式走这里）----

    def get_result(self, job_id: str) -> Optional[bytes]:
        job = self._jobs.get(job_id)
        if not job or job["status"] != "SUCCEEDED":
            return None
        if job["result_expires_at"] <= datetime.now(UTC):
            return None
        return _TINY_PDF
