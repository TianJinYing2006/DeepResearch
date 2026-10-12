"""需求 35 R4：REPORT_DELTA / REPORT_RESET 端到端（FakeStore + Worker，零网络）。

覆盖 TBD-6 ③：持久化粒度（每 N 字符合并）、断线重连（按 sequence 续发、offset 连续）、
半截失败（reset 后终局整段替换、绝不拼接）。"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fakes import FakeStore, TinyGraph, wait_terminal

from web.backend.worker import Worker


def _queued(store: FakeStore) -> str:
    run_id = "stream-" + uuid.uuid4().hex[:10]
    store.create_run(run_id, "流式主题", {"instructions": ""}, status="QUEUED",
                     timeout_at=datetime.now(UTC) + timedelta(seconds=3600))
    return run_id


def _worker(store: FakeStore, graph) -> Worker:
    return Worker(store, None, graph_factory=lambda: graph,
                  worker_id="stream-worker", lease_seconds=60,
                  heartbeat_seconds=5, poll_seconds=0)


def _report_events(store: FakeStore, run_id: str) -> list[dict]:
    return [event for event in store.get_events(run_id)
            if event["event_type"] in ("REPORT_DELTA", "REPORT_RESET")]


def test_delta_flush_granularity_and_order_before_finish():
    store = FakeStore()
    run_id = _queued(store)
    text = "".join(chr(0x4E00 + (i % 500)) for i in range(1000))  # 1000 个 CJK 字符
    assert _worker(store, TinyGraph(steps=1, stream_text=text)).run_once(run_id) is True
    wait_terminal(store, run_id)

    events = _report_events(store, run_id)
    assert events[0]["event_type"] == "REPORT_RESET"  # 协议不变式 1：开始先 reset
    deltas = [event for event in events if event["event_type"] == "REPORT_DELTA"]
    # 持久化粒度：每 400 字符合并一条（100 字符步进）⇒ 400 / 800 / 收尾 200
    assert [event["payload"]["offset"] for event in deltas] == [400, 800, 1000]
    assert "".join(event["payload"]["text"] for event in deltas) == text
    # 所有增量都在 RUN_FINISHED 之前（终局整段替换的时序前提）
    all_events = store.get_events(run_id)
    finish_index = next(index for index, event in enumerate(all_events)
                        if event["event_type"] == "RUN_FINISHED")
    assert all(index < finish_index for index, event in enumerate(all_events)
               if event["event_type"] == "REPORT_DELTA")


def test_midway_failure_reset_clears_preview_and_final_replaces():
    store = FakeStore()
    run_id = _queued(store)
    graph = TinyGraph(steps=1, stream_text="半截正文" * 100, stream_reset=True,
                      report="# 完整报告")
    assert _worker(store, graph).run_once(run_id) is True
    wait_terminal(store, run_id)

    events = _report_events(store, run_id)
    assert events[0]["event_type"] == "REPORT_RESET"
    assert events[-1]["event_type"] == "REPORT_RESET"  # 半截失败 ⇒ 再 reset（清预览）
    # 客户端按协议重建预览：最后一次 reset 之后没有增量 ⇒ 预览为空（绝不拼接半截）
    preview = ""
    for event in events:
        if event["event_type"] == "REPORT_RESET":
            preview = ""
        else:
            preview += event["payload"]["text"]
    assert preview == ""
    finished = next(event for event in store.get_events(run_id)
                    if event["event_type"] == "RUN_FINISHED")
    assert finished["payload"]["result"]["report"] == "# 完整报告"


def test_reconnect_replay_continues_from_last_event_id():
    """断线重连：带最后收到的 sequence 续发，剩余增量拼接 = 缺失部分，offset 连续。"""
    store = FakeStore()
    run_id = _queued(store)
    text = "x" * 1000
    assert _worker(store, TinyGraph(steps=1, stream_text=text)).run_once(run_id) is True
    wait_terminal(store, run_id)

    deltas = [event for event in store.get_events(run_id)
              if event["event_type"] == "REPORT_DELTA"]
    assert len(deltas) == 3
    # 客户端只收到第一条就断线：Last-Event-ID = 第一条的 sequence
    remaining = [event for event in store.get_events(run_id, after=deltas[0]["sequence"])
                 if event["event_type"] == "REPORT_DELTA"]
    assert "".join(event["payload"]["text"] for event in remaining) == text[400:]
    assert remaining[0]["payload"]["offset"] == 800


def test_stream_disabled_emits_no_report_events(monkeypatch):
    from config import config

    monkeypatch.setattr(config.report_stream, "enabled", False)
    store = FakeStore()
    run_id = _queued(store)
    assert _worker(store, TinyGraph(steps=1, stream_text="y" * 500)).run_once(run_id) is True
    wait_terminal(store, run_id)
    assert _report_events(store, run_id) == []
