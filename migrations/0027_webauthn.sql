-- 0027_webauthn.sql —— 需求 20 P1-2：Passkey（WebAuthn）凭据 + 一次性挑战
--
-- 设计（需求 20 §7.8 / §8.3）：
--   - `webauthn_credentials.credential_id` 全局主键（防跨账号重放）；
--   - 只存公钥（COSE）与 sign_count；公钥无风险，可随列表展示元数据；
--   - `sign_count` 单调递增检测克隆（0 表示认证器不提供计数器）；
--   - 挑战单独落库、一次性消费 + 过期（服务端校验，不信任客户端 challenge）；
--   - 删除最后一把凭据必须走密码确认（应用层防锁死；密码始终是备用方式）。
-- 注意：执行器 tools/migrate.sh 已用 --single-transaction 包裹，本文件不得写 BEGIN/COMMIT。

CREATE TABLE webauthn_credentials (
    credential_id  bytea PRIMARY KEY,
    user_id        text NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    public_key     bytea NOT NULL,
    sign_count     bigint NOT NULL DEFAULT 0 CHECK (sign_count >= 0),
    transports     jsonb NOT NULL DEFAULT '[]'::jsonb,
    aaguid         text,
    backup_state   boolean NOT NULL DEFAULT false,
    device_type    text,
    name           text,
    created_at     timestamptz NOT NULL DEFAULT now(),
    last_used_at   timestamptz
);

COMMENT ON TABLE webauthn_credentials IS
    '需求 20 P1-2：Passkey 凭据（公钥 + sign_count；credential_id 全局唯一）';

CREATE INDEX webauthn_credentials_user_idx ON webauthn_credentials (user_id);

CREATE TABLE webauthn_challenges (
    challenge_id   text PRIMARY KEY,
    user_id        text REFERENCES users(user_id) ON DELETE CASCADE,
    purpose        text NOT NULL CHECK (purpose IN ('register', 'authenticate')),
    challenge      bytea NOT NULL,
    created_at     timestamptz NOT NULL DEFAULT now(),
    expires_at     timestamptz NOT NULL,
    consumed_at    timestamptz
);

COMMENT ON TABLE webauthn_challenges IS
    '需求 20 P1-2：WebAuthn 一次性挑战（5 分钟过期；消费后不可重放）';

CREATE INDEX webauthn_challenges_expiry_idx ON webauthn_challenges (expires_at);
CREATE INDEX webauthn_challenges_user_idx ON webauthn_challenges (user_id, purpose);
