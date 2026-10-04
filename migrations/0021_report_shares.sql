-- 需求 26（批次 A5）：报告只读分享
--
-- 口径：分享链接 = bearer capability（拿到链接即可只读查看）。
-- - token 只存 SHA-256 摘要（同 invites / password_reset）；明文只在创建响应出现一次；
-- - 一 run 一条活跃链接（创建时撤销旧的，兄弟互斥；「重新生成」= 撤销 + 新 token）；
-- - expires_at NULL = 永不过期（显式选择；管理面标注 permanent）；
-- - 撤销/过期不删行（审计与盘点需要；purge 保留 30 天后再清）；
-- - run 删除级联失效（ON DELETE CASCADE）；账号注销由 deletion 流程显式撤销 created_by 活跃链接。
-- 约定：只前向、幂等可重跑（IF NOT EXISTS）；文件内不写 BEGIN/COMMIT（执行器 --single-transaction）。

CREATE TABLE IF NOT EXISTS report_shares (
    share_id         text PRIMARY KEY,
    token_hash       text UNIQUE NOT NULL,
    run_id           text NOT NULL REFERENCES runs ON DELETE CASCADE,
    created_by       text NOT NULL,
    created_at       timestamptz NOT NULL DEFAULT now(),
    expires_at       timestamptz,
    revoked_at       timestamptz,
    last_accessed_at timestamptz,
    access_count     int NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS report_shares_run_active_idx
    ON report_shares (run_id) WHERE revoked_at IS NULL;
CREATE INDEX IF NOT EXISTS report_shares_expires_idx
    ON report_shares (expires_at);

COMMENT ON TABLE report_shares IS
    '需求 26：报告只读分享（token 只存 hash；一 run 单活跃链接；expires_at NULL=永不过期）';
COMMENT ON COLUMN report_shares.created_by IS '创建者 user_id（无外键；注销时由 deletion 流程撤销活跃链接）';
