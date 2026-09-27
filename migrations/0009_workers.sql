-- 0009_workers.sql —— P1-3 Worker 注册表 / 心跳 / draining
--
-- 口径依据：业内 worker fleet registry 实践（注册行 + 周期心跳 + active→draining→stopped）
-- 与 docs/operations/production-readiness.md §3.6（Worker 心跳/存活指标）。
--
-- 设计：
-- - 心跳 best-effort：注册/心跳失败绝不打断 Worker 主循环（注册表是运维元数据）；
-- - readiness 以「最近 N 秒内有 active 心跳」为准（无存活 worker ⇒ 队列模式 503）；
-- - stopped / 过期行由 Worker 周期清扫删除（保留 7 天供排障）。
--
-- 注意：执行器 tools/migrate.sh 已用 --single-transaction 包裹，本文件不得写 BEGIN/COMMIT。

CREATE TABLE workers (
    worker_id         text PRIMARY KEY,
    version           text,
    hostname          text,
    status            text NOT NULL DEFAULT 'active'
                      CHECK (status IN ('active', 'draining', 'stopped')),
    started_at        timestamptz NOT NULL DEFAULT now(),
    last_heartbeat_at timestamptz NOT NULL DEFAULT now(),
    in_flight         integer NOT NULL DEFAULT 0 CHECK (in_flight >= 0),
    current_run_id    text,
    stopped_at        timestamptz,
    updated_at        timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE workers IS
    'Worker 注册表（P1-3）：心跳 best-effort；readiness/告警以 last_heartbeat_at 为准';
CREATE INDEX workers_heartbeat_idx ON workers (last_heartbeat_at DESC);
