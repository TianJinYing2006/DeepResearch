"""P0-6 终局原子落库单测（FakeStore，零 PostgreSQL）。

- `finalize_run`：状态迁移 + 终局事件 + 产物同一事务语义；
- 迁移失败（已被清扫 / 强制收口抢先）⇒ 整体回滚：不改判、不写事件、不写产物；
- `persist_terminal` 写失败（事件异常）时状态不得先行迁移（原实现的半成品问题）。
"""
from __future__ import annotations

from fakes import FakeStore

from web.backend.persistence import ACTIVE_STATUSES, persist_terminal


def _running_run(store: FakeStore, run_id: str = "atomicrun01") -> str:
    store.create_run(run_id, "t", {"instructions": ""})
    assert store.update_status(run_id, "RUNNING", allowed_from=("CREATED",))
    return run_id


def test_finalize_run_writes_status_event_and_artifacts():
    store = FakeStore()
    run_id = _running_run(store)
    ok = store.finalize_run(
        run_id, event_type="RUN_FINISHED", payload={"stop_reason": "completed"},
        sequence=None, new_status="SUCCEEDED", allowed_from=ACTIVE_STATUSES,
        fields={"stop_reason": "completed"}, artifacts={"report_md": "# 正文"})
    assert ok is True
    assert store.get_run(run_id)["status"] == "SUCCEEDED"
    assert store.get_artifact(run_id, "report_md") == "# 正文"
    assert [e["event_type"] for e in store.get_events(run_id)] == ["RUN_FINISHED"]


def test_finalize_run_rolls_back_when_status_already_terminal():
    store = FakeStore()
    run_id = _running_run(store)
    assert store.update_status(run_id, "LOST", allowed_from=("RUNNING",), stop_reason="lost")
    before_events = list(store.get_events(run_id))

    ok = store.finalize_run(
        run_id, event_type="RUN_FINISHED", payload={}, sequence=None,
        new_status="SUCCEEDED", allowed_from=ACTIVE_STATUSES,
        fields={"stop_reason": "completed"}, artifacts={"report_md": "# 迟到报告"})

    assert ok is False
    assert store.get_run(run_id)["status"] == "LOST"        # 完成先落终局，不得改判
    assert store.get_events(run_id) == before_events        # 事件未写
    assert store.get_artifact(run_id, "report_md") is None  # 产物未写


def test_persist_terminal_returns_false_on_conflict():
    store = FakeStore()
    run_id = _running_run(store)
    assert store.update_status(run_id, "LOST", allowed_from=("RUNNING",), stop_reason="lost")

    finalized = persist_terminal(store, run_id, 0, "RUN_FINISHED",
                                 {"stop_reason": "completed", "has_report": True},
                                 report="# 正文")

    assert finalized is False
    assert store.get_run(run_id)["status"] == "LOST"
    assert store.get_artifact(run_id, "report_md") is None


def test_persist_terminal_success_writes_all():
    store = FakeStore()
    run_id = _running_run(store)
    finalized = persist_terminal(
        store, run_id, 0, "RUN_FINISHED",
        {"stop_reason": "completed", "has_report": True},
        result={"report": "# 正文"}, report="# 正文",
        meta={"run_status": "success", "token_used": 10, "cost_estimate_cny": 0.01,
              "budget_used_cny": 0.01},
        topic="t")
    assert finalized is True
    row = store.get_run(run_id)
    assert row["status"] == "SUCCEEDED"
    assert row["research_status"] == "success"
    assert store.get_artifact(run_id, "report_md") == "# 正文"
    assert store.has_artifact(run_id, "export_json")


def test_persist_terminal_failure_leaves_status_unchanged():
    """事件写入失败 ⇒ 整体失败：状态不得先行迁移（原实现的半成品问题）。"""
    store = FakeStore()
    run_id = _running_run(store)
    store.fail_events = True

    raised = False
    try:
        persist_terminal(store, run_id, 0, "RUN_FINISHED", {"stop_reason": "completed"},
                         report="# 正文")
    except RuntimeError:
        raised = True

    assert raised
    assert store.get_run(run_id)["status"] == "RUNNING"
    assert store.get_artifact(run_id, "report_md") is None
