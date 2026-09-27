-- 0013_alert_deliveries.sql —— P2-6 告警状态收敛与外部交付队列
--
-- 口径依据：生产就绪清单 §3.7「告警触达值班人（IM/邮件至少一条链路）」与 §8。
--
-- 设计：
-- - `alert_states`：按 fingerprint（告警码）收敛当前状态（firing/resolved）与最近通知
--   时间 —— 同一告警持续 firing 时在冷却期内不重复外送（防刷屏），条件消失必发 resolved；
-- - `alert_deliveries`：外送队列（firing/repeat/resolved），失败指数退避重试；
--   成功后写 `delivered_at`；`attempts >= max_attempts` 时把 `next_attempt_at` 置
--   'infinity'（放弃，CLI 可手动重试）。
--
-- 注意：执行器 tools/migrate.sh 已用 --single-transaction 包裹，本文件不得写 BEGIN/COMMIT。

CREATE TABLE alert_states (
    fingerprint      text PRIMARY KEY,
    severity         text NOT NULL DEFAULT 'medium'
                     CHECK (severity IN ('low', 'medium', 'high')),
    status           text NOT NULL DEFAULT 'firing'
                     CHECK (status IN ('firing', 'resolved')),
    detail           jsonb NOT NULL DEFAULT '{}'::jsonb,
    first_seen_at    timestamptz NOT NULL DEFAULT now(),
    last_seen_at     timestamptz NOT NULL DEFAULT now(),
    last_notified_at timestamptz,
    updated_at       timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE alert_deliveries (
    delivery_id     bigserial PRIMARY KEY,
    fingerprint     text NOT NULL,
    kind            text NOT NULL CHECK (kind IN ('firing', 'repeat', 'resolved')),
    severity        text NOT NULL DEFAULT 'medium'
                    CHECK (severity IN ('low', 'medium', 'high')),
    payload         jsonb NOT NULL DEFAULT '{}'::jsonb,
    attempts        integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    max_attempts    integer NOT NULL DEFAULT 5 CHECK (max_attempts >= 1),
    next_attempt_at timestamptz NOT NULL DEFAULT now(),
    delivered_at    timestamptz,
    last_error      text,
    created_at      timestamptz NOT NULL DEFAULT now()
);

-- 交付扫描（部分索引：只关心未送达的行）
CREATE INDEX alert_deliveries_due_idx ON alert_deliveries (next_attempt_at)
    WHERE delivered_at IS NULL;
CREATE INDEX alert_deliveries_fingerprint_idx ON alert_deliveries (fingerprint, created_at DESC);

COMMENT ON TABLE alert_states IS
    'P2-6：按 fingerprint 收敛的告警状态（firing/resolved 与最近通知时间，用于去重防刷屏）';
COMMENT ON TABLE alert_deliveries IS
    'P2-6：告警外送队列（退避重试；attempts>=max_attempts 时 next_attempt_at=infinity 放弃）';
