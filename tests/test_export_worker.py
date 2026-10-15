"""需求 38 F2：export supervisor 单测（零网络；store / 结果通道 / 渲染全注入）。

覆盖：输入装配与限额（缺报告 / 超输入）、结果发布预算（写满 ⇒ export_cache_full）、
单作业处理路径（成功发布 / 确定性失败 / 超时重排 / 渲染错误重排）、子进程回收触发。
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from web.backend import export_worker as ew
from web.backend import pdf_render

RID = "run-38f2-w"


def _row(**overrides) -> dict:
    row = {
        "run_id": RID,
        "topic": "导出主题",
        "finished_at": datetime(2026, 10, 16, 8, 0, 30, tzinfo=UTC),
        "created_at": datetime(2026, 10, 16, 8, 0, 0, tzinfo=UTC),
        "research_status": "success",
        "stop_reason": "completed",
        "token_used": 1,
        "cost_estimate_cny": 0.0,
        "moderation_status": None,
        "request": {},
    }
    row.update(overrides)
    return row


class _Store:
    def __init__(self, row: dict, report_md: str = "# 报告\n\n正文。"):
        self.row = row
        self.report_md = report_md
        self.calls: list = []

    def get_run(self, run_id):
        return self.row if run_id == self.row["run_id"] else None

    def get_artifact_row(self, run_id, kind):
        if run_id != self.row["run_id"]:
            return None
        if kind == "report_md" and self.report_md:
            return {"body": self.report_md, "storage": "pg", "object_key": None}
        return None

    def finish_export_job_success(self, job_id, owner, **kwargs):
        self.calls.append(("success", job_id, kwargs))
        return True

    def finish_export_job_failure(self, job_id, owner, *, error_code, error_detail=""):
        self.calls.append(("failure", job_id, error_code))
        return True

    def requeue_export_job(self, job_id, owner, *, error_code, error_detail=""):
        self.calls.append(("requeue", job_id, error_code))
        return "QUEUED"


class _ResultRedis:
    def __init__(self, used: int = 0):
        self.used = used
        self.sets: list = []

    def info(self, section):
        return {"used_memory": self.used}

    def set(self, key, value, ex=None):
        self.sets.append((key, value, ex))


def _supervisor(store, result_redis) -> ew.ExportSupervisor:
    return ew.ExportSupervisor(store, result_redis, None, owner="owner-test")


def test_build_render_input_ok_and_limits(monkeypatch):
    store = _Store(_row())
    doc = ew.build_render_input(store, {"job_id": "j", "run_id": RID})
    assert doc["run_id"] == RID and doc["report"].startswith("# 报告")

    with pytest.raises(pdf_render.PdfRenderError) as e:
        ew.build_render_input(_Store(_row(), report_md=""), {"job_id": "j", "run_id": RID})
    assert e.value.code == "export_report_unavailable"

    monkeypatch.setattr(ew, "MAX_INPUT_BYTES", 10)
    with pytest.raises(pdf_render.PdfRenderError) as e2:
        ew.build_render_input(store, {"job_id": "j", "run_id": RID})
    assert e2.value.code == "export_too_large"


def test_publish_result_budget(monkeypatch):
    sup = _supervisor(_Store(_row()), _ResultRedis())
    sup.publish_result("j1", b"%PDF-x")
    key, value, ex = sup.result_redis.sets[0]
    assert key == "dr:export:result:j1" and value == b"%PDF-x"
    assert ex == ew.RESULT_TTL_SECONDS

    sup_full = _supervisor(_Store(_row()), _ResultRedis(used=ew.RESULT_BUDGET_BYTES))
    with pytest.raises(pdf_render.PdfRenderError) as e:
        sup_full.publish_result("j2", b"%PDF-y")
    assert e.value.code == "export_cache_full"
    assert sup_full.result_redis.sets == []


def test_process_job_success(monkeypatch):
    store = _Store(_row())
    sup = _supervisor(store, _ResultRedis())
    monkeypatch.setattr(sup, "render",
                        lambda doc: {"ok": True, "pdf": b"%PDF-1.4 x", "sha256": "abc"})
    sup.process_job({"job_id": "j1", "run_id": RID})
    kind, job_id, kwargs = store.calls[0]
    assert kind == "success" and job_id == "j1"
    assert kwargs["result_bytes"] == len(b"%PDF-1.4 x") and kwargs["result_hash"] == "abc"
    assert kwargs["result_expires_at"] > datetime.now(UTC) + timedelta(seconds=800)
    assert sup.result_redis.sets[0][0] == "dr:export:result:j1"


def test_process_job_deterministic_failure(monkeypatch):
    store = _Store(_row(), report_md="")
    sup = _supervisor(store, _ResultRedis())
    sup.process_job({"job_id": "j1", "run_id": RID})
    assert store.calls[0] == ("failure", "j1", "export_report_unavailable")


def test_process_job_timeout_requeues(monkeypatch):
    store = _Store(_row())
    sup = _supervisor(store, _ResultRedis())

    def _boom(doc):
        raise ew.RenderTimeoutError("timeout")

    monkeypatch.setattr(sup, "render", _boom)
    sup.process_job({"job_id": "j1", "run_id": RID})
    assert store.calls[0] == ("requeue", "j1", "export_render_timeout")


def test_process_job_render_error_classification(monkeypatch):
    store = _Store(_row())
    sup = _supervisor(store, _ResultRedis())
    monkeypatch.setattr(sup, "render",
                        lambda doc: {"ok": False, "error_code": "export_render_failed"})
    sup.process_job({"job_id": "j1", "run_id": RID})
    assert store.calls[0] == ("requeue", "j1", "export_render_failed")

    store2 = _Store(_row())
    sup2 = _supervisor(store2, _ResultRedis())
    monkeypatch.setattr(sup2, "render",
                        lambda doc: {"ok": False, "error_code": "export_too_many_pages"})
    sup2.process_job({"job_id": "j2", "run_id": RID})
    assert store2.calls[0] == ("failure", "j2", "export_too_many_pages")


def test_process_job_cache_full_is_terminal():
    store = _Store(_row())
    sup = _supervisor(store, _ResultRedis(used=ew.RESULT_BUDGET_BYTES))
    sup.render = lambda doc: {"ok": True, "pdf": b"x" * 10, "sha256": "h"}
    sup.process_job({"job_id": "j1", "run_id": RID})
    assert store.calls[0] == ("failure", "j1", "export_cache_full")


def test_recycle_triggers(monkeypatch):
    sup = _supervisor(_Store(_row()), _ResultRedis())
    killed: list = []
    monkeypatch.setattr(sup, "_kill_child", lambda: killed.append(1))
    sup._jobs_since_recycle = ew.RECYCLE_EVERY_JOBS - 1
    sup._recycle_if_needed()
    assert killed and sup._jobs_since_recycle == 0

    monkeypatch.setattr(ew, "_container_memory_mb", lambda: ew.RECYCLE_MEMORY_MB + 1)
    sup._recycle_if_needed()
    assert len(killed) == 2
