-- 0015_moderation_lifecycle.sql —— P0-1 审核生命周期取值补齐（修复线上阻断）
--
-- 背景（审计 P0-1）：0004 只允许 `runs.moderation_status IN ('flagged','blocked')`，
-- 但 0014 的申诉语义与 `web/backend/appeals.py` 在 accepted 时写 `cleared`
-- ⇒ 在真实 PostgreSQL 上会触发 CHECK 违例：申诉已 accepted、run 仍 flagged、
-- 导出永久阻断、审计与任务状态不一致。
--
-- 本迁移补齐两个取值域：
-- - `runs.moderation_status`：+ `cleared`（申诉通过）与 `under_review`（审核
--   provider 降级隔离，P0-3；导出同样阻断，但语义是「待审核」而非「已命中」）；
-- - `moderation_records.kind`：+ `appeal_accepted` / `appeal_rejected`（申诉决策
--   证据，appeals.py 已在写但原约束不允许）+ `output_decision`（不可变审核决定，
--   P0-4：provider / policy / degraded / failure_reason 一次落库）。
--
-- 注意：执行器 tools/migrate.sh 已用 --single-transaction 包裹，本文件不得写 BEGIN/COMMIT。

-- 1) runs.moderation_status：先找出现存 CHECK（名字可能是自动生成的）再替换
DO $$
DECLARE
    conname text;
BEGIN
    FOR conname IN
        SELECT c.conname
          FROM pg_constraint c
         WHERE c.conrelid = 'runs'::regclass
           AND c.contype = 'c'
           AND pg_get_constraintdef(c.oid) LIKE '%moderation_status%'
    LOOP
        EXECUTE format('ALTER TABLE runs DROP CONSTRAINT %I', conname);
    END LOOP;
END $$;

ALTER TABLE runs
    ADD CONSTRAINT runs_moderation_status_check
    CHECK (moderation_status IS NULL
           OR moderation_status IN ('flagged', 'blocked', 'cleared', 'under_review'));

-- 2) moderation_records.kind：补齐申诉决策与不可变审核决定
DO $$
DECLARE
    conname text;
BEGIN
    FOR conname IN
        SELECT c.conname
          FROM pg_constraint c
         WHERE c.conrelid = 'moderation_records'::regclass
           AND c.contype = 'c'
           AND pg_get_constraintdef(c.oid) LIKE '%kind%'
    LOOP
        EXECUTE format('ALTER TABLE moderation_records DROP CONSTRAINT %I', conname);
    END LOOP;
END $$;

ALTER TABLE moderation_records
    ADD CONSTRAINT moderation_records_kind_check
    CHECK (kind IN ('input_blocked', 'output_flagged', 'appeal',
                    'appeal_accepted', 'appeal_rejected', 'output_decision',
                    'admin_action'));

COMMENT ON COLUMN runs.moderation_status IS
    '输出审核状态：NULL=未标记 / flagged=命中待复核 / under_review=审核降级隔离 / blocked=阻断 / cleared=申诉通过；导出仅放行 NULL 与 cleared';
COMMENT ON COLUMN moderation_records.kind IS
    '审核证据类型（append-only）：input_blocked / output_flagged / appeal / appeal_accepted / appeal_rejected / output_decision / admin_action';
