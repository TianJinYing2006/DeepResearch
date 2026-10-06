-- 0004_moderation.sql —— L3-A 内容安全（P7-A）
--
-- 口径依据：docs/requirements/10-l3-production.md §5.7（P7）与 §5.2（表清单）。
-- 诚实边界：本批只落**记录与标记**能力（预检 / 输出标记 / 申诉 / 管理动作），
-- 真正的内容审核服务与人工复核流程属 P7-B；记录表保留完整证据以便追责与申诉。

CREATE TABLE moderation_records (
    id         bigserial PRIMARY KEY,
    user_id    text REFERENCES users(user_id) ON DELETE SET NULL,
    run_id     text REFERENCES runs(run_id) ON DELETE SET NULL,
    kind       text NOT NULL CHECK (kind IN ('input_blocked', 'output_flagged', 'appeal', 'admin_action')),
    detail     jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE moderation_records IS '内容审核与申诉记录（只追加）；用户/任务删除后仅脱钩不清空';
CREATE INDEX moderation_user_idx ON moderation_records (user_id, created_at DESC);
CREATE INDEX moderation_kind_idx ON moderation_records (kind, created_at DESC);
CREATE INDEX moderation_run_idx ON moderation_records (run_id);

-- 输出侧标记：flagged = 报告命中预检词（仍保留正文，等待人工复核/申诉）
ALTER TABLE runs
    ADD COLUMN moderation_status text
    CHECK (moderation_status IS NULL OR moderation_status IN ('flagged', 'blocked'));
