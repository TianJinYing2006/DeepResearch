"""P2-6 告警外送测试（状态机 / 退避 / webhook 体格式 / Worker 集成 / PG 契约）。"""
from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fakes import FakeQueue, FakeStore

from web.backend import alerts as alerts_mod
from web.backend.alerts import (
    build_webhook_body,
    collect_alerts,
    process_deliveries_once,
    sync_alerts,
)

DSN = os.getenv("DR_TEST_DATABASE_URL", "").strip()

THRESHOLDS = {"http_5xx_rate_pct": 2.0, "queue_depth": 20, "stale_runs": 1,
              "monthly_pct": 80.0}


def _queue_alert(value: float = 30):
    return collect_alerts(thresholds=THRESHOLDS, queue_depth=value)


def test_collect_alerts_codes_and_5xx_skip():
    alerts = collect_alerts(thresholds=THRESHOLDS, http=None, stale_leases=1,
                            execution_mode="queue", workers_live=0,
                            month_cost=90, month_budget=100)
    codes = {a["code"] for a in alerts}
    assert {"stale_leases", "worker_heartbeat_missing", "monthly_budget"} <= codes
    assert "http_5xx_rate" not in codes  # Worker 无 HTTP 指标时不判 5xx

    http_alerts = collect_alerts(thresholds=THRESHOLDS,
                                 http={"2xx": 0, "4xx": 0, "5xx": 10})
    assert http_alerts[0]["code"] == "http_5xx_rate"


def test_sync_firing_repeat_cooldown_and_resolve(monkeypatch):
    store = FakeStore()
    monkeypatch.setenv("DR_ALERT_REPEAT_MINUTES", "30")
    start = datetime.now(UTC)

    summary = sync_alerts(store, _queue_alert(), now=start, webhook_enabled=True)
    assert summary["firing"] == ["queue_depth"]
    assert [d["kind"] for d in store.alert_deliveries] == ["firing"]
    assert store.get_alert_state("queue_depth")["status"] == "firing"

    # 冷却期内重复判定：仅刷新 detail，不再外送
    summary = sync_alerts(store, _queue_alert(40), now=start + timedelta(minutes=10),
                          webhook_enabled=True)
    assert summary["repeat"] == [] and summary["firing"] == []
    assert len(store.alert_deliveries) == 1
    assert store.get_alert_state("queue_depth")["detail"]["value"] == 40

    # 冷却期后：repeat
    summary = sync_alerts(store, _queue_alert(50), now=start + timedelta(minutes=31),
                          webhook_enabled=True)
    assert summary["repeat"] == ["queue_depth"]
    assert [d["kind"] for d in store.alert_deliveries] == ["firing", "repeat"]

    # 条件消失：resolved 必发
    summary = sync_alerts(store, [], now=start + timedelta(minutes=32),
                          webhook_enabled=True)
    assert summary["resolved"] == ["queue_depth"]
    assert store.get_alert_state("queue_depth")["status"] == "resolved"
    assert store.alert_deliveries[-1]["kind"] == "resolved"

    # 再次触发：新周期 firing
    summary = sync_alerts(store, _queue_alert(), now=start + timedelta(minutes=33),
                          webhook_enabled=True)
    assert summary["firing"] == ["queue_depth"]


def test_sync_without_webhook_only_tracks_state():
    store = FakeStore()
    summary = sync_alerts(store, _queue_alert(), webhook_enabled=False)
    assert summary["firing"] == ["queue_depth"]
    assert store.alert_deliveries == []
    assert store.get_alert_state("queue_depth")["status"] == "firing"


def test_process_deliveries_backoff_and_give_up(monkeypatch):
    store = FakeStore()
    monkeypatch.setenv("DR_ALERT_WEBHOOK_URL", "https://example.com/hook")
    monkeypatch.setenv("DR_ALERT_RETRY_BASE_SECONDS", "30")
    store.enqueue_alert_delivery("queue_depth", "firing", "medium",
                                 payload={"code": "queue_depth"}, max_attempts=2)

    def failing(url, body):
        raise RuntimeError("boom")

    summary = process_deliveries_once(store, transport=failing)
    assert summary["claimed"] == 1 and summary["failed"] == 1
    row = store.alert_deliveries[0]
    assert row["attempts"] == 1 and row["last_error"].startswith("RuntimeError")
    assert row["next_attempt_at"] > datetime.now(UTC) + timedelta(seconds=20)

    # 模拟退避到期：第二次失败达到 max_attempts ⇒ 放弃（given_up）
    row["next_attempt_at"] = datetime.now(UTC)
    summary = process_deliveries_once(store, transport=failing)
    assert summary["given_up"] == 1
    assert row["given_up"] is True
    assert "give up after 2 attempts" in row["last_error"]

    # 人工重试：attempts/given_up 复位并成功送达
    assert store.retry_alert_delivery(row["delivery_id"]) is True
    assert row["attempts"] == 0 and row["given_up"] is False
    summary = process_deliveries_once(store, transport=lambda url, body: None)
    assert summary["delivered"] == 1
    assert row["delivered_at"] is not None


def test_process_deliveries_skips_without_webhook(monkeypatch):
    store = FakeStore()
    monkeypatch.delenv("DR_ALERT_WEBHOOK_URL", raising=False)
    store.enqueue_alert_delivery("queue_depth", "firing", "medium")
    summary = process_deliveries_once(store)
    assert summary["skipped"] == "no_webhook" and summary["claimed"] == 0
    assert store.alert_deliveries[0]["attempts"] == 0


def test_webhook_body_per_provider():
    delivery = {"fingerprint": "queue_depth", "kind": "firing", "severity": "medium",
                "payload": {"message": "队列积压", "value": 30, "threshold": 20}}
    feishu = build_webhook_body("https://open.feishu.cn/open-apis/bot/v2/hook/x",
                                delivery)
    assert feishu["msg_type"] == "text"
    assert "queue_depth" in feishu["content"]["text"]

    ding = build_webhook_body("https://oapi.dingtalk.com/robot/send?access_token=x",
                              delivery)
    assert ding["msgtype"] == "markdown"

    wecom = build_webhook_body("https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=x",
                               delivery)
    assert wecom["msgtype"] == "markdown"

    generic = build_webhook_body("https://hooks.example.com/dr", delivery)
    assert generic["source"] == "deepresearch"
    assert generic["fingerprint"] == "queue_depth"
    assert generic["kind"] == "firing"


def test_worker_sync_alerts_once(monkeypatch):
    from web.backend.worker import Worker

    monkeypatch.setenv("DR_ALERT_WEBHOOK_URL", "https://hooks.example.com/dr")
    monkeypatch.setenv("DR_ALERT_MAX_ATTEMPTS", "5")
    posted: list[dict] = []
    monkeypatch.setattr(alerts_mod, "_post_json",
                        lambda url, body: posted.append(body))

    store = FakeStore()
    store.create_run("stale001", "t", {}, status="QUEUED")
    assert store.claim_run("stale001", "dead-worker", -1) is not None
    worker = Worker(store, FakeQueue(), graph_factory=lambda: None)

    summary = worker.sync_alerts_once()

    assert "stale_leases" in summary["firing"]
    assert summary["delivery"]["delivered"] >= 1
    assert posted and "stale_leases" in posted[0]["text"]
    assert store.get_alert_state("stale_leases")["status"] == "firing"


@pytest.mark.skipif(not DSN, reason="DR_TEST_DATABASE_URL 未设置（需要真实 PostgreSQL）")
def test_alert_pg_roundtrip():
    import psycopg

    from web.backend.store import RunStore

    store = RunStore(DSN)
    fingerprint = f"test-alert-{uuid.uuid4().hex[:8]}"
    try:
        state = store.upsert_alert_state(fingerprint, severity="high", status="firing",
                                         detail={"code": fingerprint},
                                         notified_at=datetime.now(UTC))
        assert state["status"] == "firing" and state["last_notified_at"] is not None

        delivery_id = store.enqueue_alert_delivery(
            fingerprint, "firing", "high", payload={"code": fingerprint}, max_attempts=1)
        claimed = [d for d in store.claim_due_alert_deliveries(limit=50)
                   if d["delivery_id"] == delivery_id]
        assert len(claimed) == 1 and claimed[0]["attempts"] == 1

        assert store.fail_alert_delivery(delivery_id, delay_seconds=0,
                                         error="test-fail") is True
        row = next(d for d in store.list_alert_deliveries(undelivered_only=True, limit=200)
                   if d["delivery_id"] == delivery_id)
        assert row["given_up"] is True  # attempts=1 >= max_attempts=1 ⇒ 放弃

        assert store.retry_alert_delivery(delivery_id) is True
        reclaimed = [d for d in store.claim_due_alert_deliveries(limit=50)
                     if d["delivery_id"] == delivery_id]
        assert reclaimed and reclaimed[0]["attempts"] == 1
        assert reclaimed[0]["given_up"] is False
        assert store.finish_alert_delivery(delivery_id) is True

        resolved = store.upsert_alert_state(fingerprint, severity="high",
                                            status="resolved",
                                            detail={"resolved": True},
                                            notified_at=datetime.now(UTC))
        assert resolved["status"] == "resolved"
    finally:
        store.close()
        with psycopg.connect(DSN) as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM alert_deliveries WHERE fingerprint = %s",
                        (fingerprint,))
            cur.execute("DELETE FROM alert_states WHERE fingerprint = %s", (fingerprint,))
