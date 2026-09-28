-- 0016_quota_reservations.sql —— P0-2 预留式月度预算
--
-- 背景（审计 P0-2）：原准入只检查 `SUM(runs.cost_estimate_cny)`，而新任务的
-- `cost_estimate_cny` 在创建时为 0、由 Worker 执行后才回写 ⇒ 并发下多个任务
-- 看到同一「已用 0」，可一起越过月度预算闸。
--
-- 本表把「预计成本」在准入事务内**预留**（hold），任务终局时结算为实际值：
--   reserved → settled（actual_cny = 终局成本，released_cny = 剩余释放）
-- 预算占用口径（见 store.create_run_admitted / _month_committed_sql）：
--   committed = SUM(runs.cost_estimate_cny 本月)
--             + SUM(GREATEST(reserved.hold - run.cost_estimate_cny, 0))  -- 仅 status='reserved'
-- 即：未结算的 hold 与已发生成本取较大者，不双重计数；终局后 hold 释放。
-- 领取 run 的 Worker 崩溃等场景由准入事务内的「终局 run 自动结算」兜底。
--
-- 注意：执行器 tools/migrate.sh 已用 --single-transaction 包裹，本文件不得写 BEGIN/COMMIT。

CREATE TABLE quota_reservations (
    reservation_id text PRIMARY KEY,
    run_id         text NOT NULL UNIQUE REFERENCES runs(run_id) ON DELETE CASCADE,
    user_id        text,
    period         date NOT NULL,
    reserved_cny   numeric(12,6) NOT NULL CHECK (reserved_cny >= 0),
    actual_cny     numeric(12,6) NOT NULL DEFAULT 0 CHECK (actual_cny >= 0),
    released_cny   numeric(12,6) NOT NULL DEFAULT 0 CHECK (released_cny >= 0),
    status         text NOT NULL DEFAULT 'reserved'
                   CHECK (status IN ('reserved', 'settled')),
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE quota_reservations IS
    'P0-2 预留式月度预算：准入时 hold 预计成本（reserved_cny），终局同事务结算（actual/released，settled）';
CREATE INDEX quota_reservations_active_idx
    ON quota_reservations (period) WHERE status = 'reserved';
CREATE INDEX quota_reservations_user_idx ON quota_reservations (user_id, period);
