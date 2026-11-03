-- 0028_mfa_replay_guard.sql —— 需求 42：TOTP 验证码防重放（RFC 6238 §5.2）
--
-- 背景：`totp.verify_totp` 纯无状态、接受 ±1 个时间步，同一 6 位码在有效窗口内
-- 可被重复用于不同的半程会话（审计实测 200/200）。RFC 6238 §5.2 要求验证方
-- **必须拒绝已使用过的 OTP**。
--
-- 设计：
--   - 在因子上记录 `last_used_step`（TOTP 时间步 = floor(epoch / 30)）；
--   - 校验成功后以**单条原子 UPDATE** 推进（仅当 `last_used_step < 本次步` 才成功），
--     并发下同一码只有一个请求成功（见 store.consume_mfa_factor_step）；
--   - 列为 NULL 表示从未消费：历史因子升级后首次验证即可消费，无需回填；
--   - 恢复码路径不受影响（仍走 recovery_codes.used_at 的单次消费语义）。
-- 注意：执行器 tools/migrate.sh 已用 --single-transaction 包裹，本文件不得写 BEGIN/COMMIT。

ALTER TABLE mfa_factors ADD COLUMN IF NOT EXISTS last_used_step bigint;

COMMENT ON COLUMN mfa_factors.last_used_step IS
    '需求 42：最近一次成功消费的 TOTP 时间步（floor(epoch/30)）；用于拒绝重放，NULL=尚未使用';
