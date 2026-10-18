-- 0026_mfa.sql —— 需求 20 P1-3：TOTP MFA（因子 + 恢复码 + 会话「MFA 已验」标记）
--
-- 设计（需求 20 §7.8 / §8.3）：
--   - TOTP secret **可逆加密**存储（AES-256-GCM，密钥在 env `DR_MFA_SECRET_KEY`）
--     ⇒ 库/备份泄露不直接可用；**不得**明文、**不得**哈希（哈希无法还原供校验）；
--   - 恢复码只存 SHA-256 摘要、**单次使用**（`used_at`）；
--   - 登录 step-up：MFA 用户的会话先落 `mfa_verified=false`，第二步验证后置 true；
--     非 MFA 用户会话恒为 true（列默认值）⇒ 既有行为零变化；
--   - 锁死防护由应用层保证（删最后一个因子需先关闭 MFA；恢复码 10 枚一次性）。
-- 注意：执行器 tools/migrate.sh 已用 --single-transaction 包裹，本文件不得写 BEGIN/COMMIT。

CREATE TABLE mfa_factors (
    factor_id        text PRIMARY KEY,
    user_id          text NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    type             text NOT NULL DEFAULT 'totp' CHECK (type IN ('totp')),
    secret_encrypted bytea NOT NULL,
    name             text,
    created_at       timestamptz NOT NULL DEFAULT now(),
    confirmed_at     timestamptz,
    last_used_at     timestamptz
);

COMMENT ON TABLE mfa_factors IS
    '需求 20 P1-3：MFA 因子（TOTP secret 为 AES-256-GCM 密文，非明文 / 非哈希）';

CREATE INDEX mfa_factors_user_idx ON mfa_factors (user_id);

-- 同一用户同一类型最多一个**已确认**因子（未确认的暂存行可多次覆盖，防重复绑定）
CREATE UNIQUE INDEX mfa_factors_confirmed_uk ON mfa_factors (user_id, type)
    WHERE confirmed_at IS NOT NULL;

CREATE TABLE recovery_codes (
    id         bigserial PRIMARY KEY,
    user_id    text NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    code_hash  text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    used_at    timestamptz
);

COMMENT ON TABLE recovery_codes IS
    '需求 20 P1-3：MFA 恢复码（只存 SHA-256 摘要；单次使用）';

CREATE UNIQUE INDEX recovery_codes_hash_uk ON recovery_codes (user_id, code_hash);
CREATE INDEX recovery_codes_user_idx ON recovery_codes (user_id);

-- 登录 step-up 标记：默认 true（非 MFA 用户零行为变化）
ALTER TABLE sessions ADD COLUMN mfa_verified boolean NOT NULL DEFAULT true;
