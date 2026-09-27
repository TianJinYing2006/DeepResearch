-- 0005_account_deletion_outbox.sql —— P0-7 注销 durable outbox
--
-- 口径依据：docs/requirements/10-l3-production.md §5.7（P7）与
-- docs/operations/production-readiness.md §3.5（处置链路）/§5（恢复演练）。
--
-- 设计（业内实践：erasure saga + transactional outbox）：
-- - `account_deletions` 是**删除台账**（erasure ledger）：不含 PII（仅内部 user_id），
--   长期保留；备份恢复后按此重放删除（"delete on resurrection"，当前为人工 runbook）；
-- - `deletion_outbox` 承载**外部系统清理**（当前仅 Qdrant）：与注销登记同事务写入，
--   由 Worker 带租约领取、指数退避重试、耗尽置 abandoned（绝不静默丢弃）；
-- - 幂等：`(request_id, target)` 唯一；"已经不存在"视为成功。
--
-- 注意：执行器 tools/migrate.sh 已用 --single-transaction 包裹，本文件不得写 BEGIN/COMMIT。

CREATE TABLE account_deletions (
    request_id   text PRIMARY KEY,
    user_id      text NOT NULL,
    status       text NOT NULL DEFAULT 'pending'
                 CHECK (status IN ('pending', 'in_progress', 'completed', 'abandoned')),
    attempts     integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    last_error   text,
    requested_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    updated_at   timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE account_deletions IS
    '注销请求台账（P0-7 erasure ledger）：无 PII，长期保留；备份恢复后按此重放删除';
CREATE INDEX account_deletions_status_idx ON account_deletions (status, requested_at);

CREATE TABLE deletion_outbox (
    id               bigserial PRIMARY KEY,
    request_id       text NOT NULL REFERENCES account_deletions(request_id) ON DELETE CASCADE,
    target           text NOT NULL CHECK (target IN ('qdrant')),
    payload          jsonb NOT NULL DEFAULT '{}'::jsonb,
    status           text NOT NULL DEFAULT 'pending'
                     CHECK (status IN ('pending', 'in_progress', 'done', 'abandoned')),
    attempts         integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    next_attempt_at  timestamptz NOT NULL DEFAULT now(),
    lease_expires_at timestamptz,
    claimed_by       text,
    last_error       text,
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE deletion_outbox IS
    '外部系统清理 outbox（P0-7）：与注销登记同事务写入；Worker 领取重试，耗尽置 abandoned';
CREATE UNIQUE INDEX deletion_outbox_request_target_uk ON deletion_outbox (request_id, target);
CREATE INDEX deletion_outbox_due_idx ON deletion_outbox (next_attempt_at)
    WHERE status IN ('pending', 'in_progress');
