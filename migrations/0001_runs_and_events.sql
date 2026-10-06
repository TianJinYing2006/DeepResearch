-- 0001_runs_and_events.sql —— L3-A 任务持久化第一批（P2-A）
--
-- 口径依据：
--   docs/requirements/10-l3-production.md §5.2（数据模型）/ §5.9（状态迁移、幂等、租约、预算）
--   docs/decisions/0009-l3-multiuser-production.md（PostgreSQL 为唯一事实来源）
--
-- 本批只建「任务不丢」所需的两张表；run_artifacts / run_checkpoints / users / quotas
-- 等表在后续迁移中增加（P2-B 起）。
--
-- 注意：执行器 tools/migrate.sh 已用 --single-transaction 包裹，本文件不得写 BEGIN/COMMIT。

CREATE TABLE runs (
    run_id              text PRIMARY KEY,
    user_id             text,
    tenant_id           text,
    status              text NOT NULL DEFAULT 'CREATED'
                        CHECK (status IN ('CREATED','QUEUED','RUNNING','CANCEL_REQUESTED',
                                          'SUCCEEDED','FAILED','CANCELLED','TIMED_OUT','LOST')),
    research_status     text
                        CHECK (research_status IS NULL OR research_status IN ('success','degraded','failed')),
    stop_reason         text,
    current_node        text,
    topic               text NOT NULL,
    request             jsonb NOT NULL DEFAULT '{}'::jsonb,
    token_used          bigint NOT NULL DEFAULT 0 CHECK (token_used >= 0),
    cost_estimate_cny   numeric(10,4) NOT NULL DEFAULT 0 CHECK (cost_estimate_cny >= 0),
    budget_limit_cny    numeric(10,2) CHECK (budget_limit_cny IS NULL OR budget_limit_cny > 0),
    budget_used_cny     numeric(10,4) NOT NULL DEFAULT 0 CHECK (budget_used_cny >= 0),
    attempt             integer NOT NULL DEFAULT 1 CHECK (attempt >= 1),
    retry_of            text REFERENCES runs(run_id) ON DELETE SET NULL,
    idempotency_key     text,
    worker_id           text,
    worker_status       text,
    lease_expires_at    timestamptz,
    timeout_at          timestamptz,
    hard_deadline_at    timestamptz,
    cancel_requested_at timestamptz,
    created_at          timestamptz NOT NULL DEFAULT now(),
    queued_at           timestamptz,
    started_at          timestamptz,
    finished_at         timestamptz,
    CHECK (finished_at IS NULL OR finished_at >= created_at)
);

COMMENT ON TABLE runs IS 'L3 任务权威状态（唯一事实来源）；状态机见需求 10 §5.9.1';
COMMENT ON COLUMN runs.research_status IS '研究产出侧三态（W8 口径）：success / degraded / failed';
COMMENT ON COLUMN runs.stop_reason IS '停止原因：running / completed / cancelled / error / timeout / budget_exceeded；不写研究侧 run_status';

-- 创建幂等：同一用户同一幂等键只允许一个 run；NULL 键不参与（内部导入 / 历史）
CREATE UNIQUE INDEX runs_idempotency_uk
    ON runs (user_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;

-- 历史列表（P6）：按用户 + 时间倒序分页
CREATE INDEX runs_user_created_idx ON runs (user_id, created_at DESC);

-- 活跃任务 / 租约扫描（P3 worker 崩溃恢复）
CREATE INDEX runs_active_idx ON runs (status)
    WHERE status IN ('CREATED','QUEUED','RUNNING','CANCEL_REQUESTED');
CREATE INDEX runs_lease_idx ON runs (lease_expires_at)
    WHERE lease_expires_at IS NOT NULL AND status IN ('RUNNING','CANCEL_REQUESTED');

CREATE TABLE run_events (
    run_id      text NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    sequence    bigint NOT NULL CHECK (sequence >= 0),
    event_id    uuid NOT NULL DEFAULT gen_random_uuid(),
    event_type  text NOT NULL,
    payload     jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id, sequence)
);

COMMENT ON TABLE run_events IS 'SSE 事件日志（权威）；sequence 单调递增，作为 Last-Event-ID 续传依据';
COMMENT ON COLUMN run_events.sequence IS '单 run 内从 0 单调递增（与现内存态帧下标语义一致）';

CREATE UNIQUE INDEX run_events_event_id_uk ON run_events (event_id);
CREATE INDEX run_events_created_idx ON run_events (created_at);
