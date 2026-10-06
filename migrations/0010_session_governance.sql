-- 0010_session_governance.sql —— P1-10 会话治理与密码重置 token
--
-- 口径依据：OWASP ASVS V7（会话管理）与 Forgot Password Cheat Sheet：
-- - 会话可查看/可远程终止（终止他人需重认证）；记录 last_seen / IP / UA；
-- - 重置 token：≥128bit 随机、**只存 SHA-256 摘要**、单次原子消费、30 分钟过期、
--   使用后吊销全部会话；L3-A 无邮件通道 ⇒ 管理员 CLI 发放（接口按未来邮件通道设计）。
--
-- 注意：执行器 tools/migrate.sh 已用 --single-transaction 包裹，本文件不得写 BEGIN/COMMIT。

ALTER TABLE sessions ADD COLUMN session_id text NOT NULL DEFAULT gen_random_uuid()::text;
ALTER TABLE sessions ADD COLUMN last_seen_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE sessions ADD COLUMN ip text;
ALTER TABLE sessions ADD COLUMN user_agent text;

COMMENT ON COLUMN sessions.session_id IS '对外可见的会话标识（P1-10；token_hash 不外泄）';
CREATE UNIQUE INDEX sessions_session_id_uk ON sessions (session_id);

CREATE TABLE password_reset_tokens (
    token_hash  text PRIMARY KEY,
    user_id     text NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    created_by  text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    expires_at  timestamptz NOT NULL,
    consumed_at timestamptz
);

COMMENT ON TABLE password_reset_tokens IS
    '密码重置 token（P1-10）：只存 SHA-256 摘要；单次消费；使用后吊销全部会话';
CREATE INDEX password_reset_user_idx ON password_reset_tokens (user_id, created_at DESC);
CREATE INDEX password_reset_expires_idx ON password_reset_tokens (expires_at)
    WHERE consumed_at IS NULL;
