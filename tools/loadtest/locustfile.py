"""DeepResearch 容量标定压测脚本（P2-7，**手动执行，不进 CI**）。

安全边界与用法见 `tools/loadtest/README.md`：
- 只针对 staging / 本地联调环境；
- 建议 worker 设 `DR_LOADTEST_GRAPH=1`（假图，零 LLM 费用、零外部调用），
  压的是 API / 队列 / SSE / PostgreSQL 的管道容量，不是研究质量；
- 提交被配额 / 限流 / 幂等拒绝（409 / 429）按**预期拒绝**计入成功，
  避免污染错误率指标（拒绝数在 Locust 报告里看 `POST /api/research` 与
  `[rejected]` 名称的对比）。

环境变量：
- `DR_LOADTEST_EMAIL` / `DR_LOADTEST_PASSWORD`：开启鉴权时必填（登录 + CSRF 双提交）；
- `DR_LOADTEST_TOPIC_PREFIX`：主题前缀（默认 `压测`，便于清理/识别）；
- `DR_LOADTEST_STREAM_SECONDS`：单次 SSE 读取上限（默认 5s）。

启动：
    locust -f tools/loadtest/locustfile.py --host http://127.0.0.1:8000
"""
from __future__ import annotations

import os
import time
import uuid

from locust import HttpUser, between, task


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def _env_float(name: str, default: float) -> float:
    try:
        return float(_env(name, str(default)))
    except ValueError:
        return default


class ResearchUser(HttpUser):
    """提交 → 尾随 SSE → 端点探活；按任务权重混合读写路径。"""

    wait_time = between(0.2, 1.0)

    def on_start(self) -> None:
        self.csrf_token = ""
        self.last_run_id = ""
        self.topic_prefix = _env("DR_LOADTEST_TOPIC_PREFIX", "压测")
        email = _env("DR_LOADTEST_EMAIL")
        if email:
            response = self.client.post(
                "/api/auth/login",
                json={"email": email, "password": _env("DR_LOADTEST_PASSWORD")},
                name="POST /api/auth/login",
            )
            if response.status_code == 200:
                self.csrf_token = self.client.cookies.get("dr_csrf", "")
            else:
                response.failure(f"login failed: {response.status_code}")

    def _headers(self) -> dict[str, str]:
        return {"X-CSRF-Token": self.csrf_token} if self.csrf_token else {}

    @task(10)
    def submit(self) -> None:
        topic = f"{self.topic_prefix}-{uuid.uuid4().hex[:10]}"
        with self.client.post(
            "/api/research",
            json={"topic": topic, "instructions": ""},
            headers=self._headers(),
            name="POST /api/research",
            catch_response=True,
        ) as response:
            if response.status_code == 200:
                self.last_run_id = response.json().get("run_id", "")
                response.success()
            elif response.status_code in (409, 429):
                # 配额 / 幂等冲突 / 限流：压测预期结果，单独计名，不计失败
                response.success()
            else:
                response.failure(f"unexpected {response.status_code}: {response.text[:120]}")

    @task(6)
    def stream(self) -> None:
        if not self.last_run_id:
            return
        deadline = time.time() + _env_float("DR_LOADTEST_STREAM_SECONDS", 5.0)
        with self.client.get(
            f"/api/research/{self.last_run_id}/stream",
            stream=True,
            name="GET /api/research/:id/stream",
            catch_response=True,
        ) as response:
            if response.status_code != 200:
                response.failure(f"stream http {response.status_code}")
                return
            try:
                for line in response.iter_lines(decode_unicode=True):
                    if time.time() > deadline:
                        break
            except Exception as exc:  # noqa: BLE001 —— 客户端侧读流异常单独计数
                response.failure(f"stream read failed: {type(exc).__name__}: {exc}")
                return
            response.success()

    @task(2)
    def readiness(self) -> None:
        self.client.get("/api/health/ready", name="GET /api/health/ready")

    @task(1)
    def ops_metrics(self) -> None:
        self.client.get("/api/metrics", name="GET /api/metrics")
