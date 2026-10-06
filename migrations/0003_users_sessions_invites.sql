-- 0003_users_sessions_invites.sql —— L3-A 账号与会话（P4-A）
--
-- 口径依据：docs/requirements/10-l3-production.md §3.1（邀请制、邮箱+密码+httpOnly Session）
-- 与 §5.13（配置）。安全要点：
-- - 密码只存 Argon2id 哈希；会话/邀请码只存 SHA-256 摘要（库泄露不直接可用）；
-- - 邮箱唯一性按 `lower(email)`（大小写不敏感）；
-- - 邀请码一次性：`used_by`/`used_at`，可过期、可撤销（撤销 = `revoked_at` 置位）。

CREATE TABLE users (
    user_id       text PRIMARY KEY,
    email         text NOT NULL,
    password_hash text NOT NULL,
    status        text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'banned')),
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now(),
    last_login_at timestamptz
);

COMMENT ON TABLE users IS 'L3 用户（P4-A）；密码为 Argon2id 哈希，邮箱唯一性大小写不敏感';
CREATE UNIQUE INDEX users_email_lower_uk ON users (lower(email));

CREATE TABLE sessions (
    token_hash text PRIMARY KEY,
    user_id    text NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL
);

COMMENT ON TABLE sessions IS '会话（httpOnly Cookie 持有原始 token；库内只存 SHA-256 摘要）';
CREATE INDEX sessions_user_idx ON sessions (user_id);
CREATE INDEX sessions_expires_idx ON sessions (expires_at);

CREATE TABLE invites (
    code_hash  text PRIMARY KEY,
    created_by text,
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz,
    used_by    text REFERENCES users(user_id) ON DELETE SET NULL,
    used_at    timestamptz,
    revoked_at timestamptz
);

COMMENT ON TABLE invites IS '邀请码（一次性；只存摘要，可过期/可撤销）';
CREATE INDEX invites_unused_idx ON invites (created_at)
    WHERE used_at IS NULL AND revoked_at IS NULL;

-- runs.user_id 建立外键：NULL 允许（P4 之前的匿名历史）；用户删除策略在 P7 定案，
-- 此处用 ON DELETE SET NULL 保留任务记录、仅脱钩所有者。
ALTER TABLE runs
    ADD CONSTRAINT runs_user_fk FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE SET NULL;
