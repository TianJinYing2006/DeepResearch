-- 0014_moderation_appeals.sql —— P2-5b 申诉/复核状态机
--
-- 口径依据：生产就绪清单 §3.5（审核记录与申诉入口）与 §7.2（人工复核）。
-- `moderation_records` 仍是 append-only 证据链；本表是**工作流状态的真源**：
--   pending → reviewing → accepted / rejected
-- 决策语义：
-- - accepted：任务 `moderation_status` 置 `cleared`（导出闸放行；事件流历史仍按落库时脱敏）；
-- - rejected：任务维持 `flagged`，仅留决策记录。
-- `sla_due_at` 由应用写入（`DR_APPEAL_SLA_HOURS`，默认 72h），超期未决可告警。
--
-- 注意：执行器 tools/migrate.sh 已用 --single-transaction 包裹，本文件不得写 BEGIN/COMMIT。

CREATE TABLE moderation_appeals (
    appeal_id     text PRIMARY KEY,
    run_id        text REFERENCES runs(run_id) ON DELETE CASCADE,
    user_id       text,
    message       text NOT NULL,
    status        text NOT NULL DEFAULT 'pending'
                  CHECK (status IN ('pending', 'reviewing', 'accepted', 'rejected')),
    decision_note text,
    reviewed_by   text,
    sla_due_at    timestamptz,
    decided_at    timestamptz,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now()
);

-- 同一 run + 同一用户只允许一条申诉（run_id 为 NULL 的通用申诉不受限）
CREATE UNIQUE INDEX moderation_appeals_run_user_idx
    ON moderation_appeals (run_id, user_id);
CREATE INDEX moderation_appeals_status_idx
    ON moderation_appeals (status, sla_due_at);

COMMENT ON TABLE moderation_appeals IS
    'P2-5b：申诉/复核状态机（pending→reviewing→accepted/rejected；accepted 置 run=cleared）';
