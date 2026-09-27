-- 0008_request_hash.sql —— P1-1 幂等键请求指纹
--
-- 口径依据：IETF Idempotency-Key 草案 / Stripe 幂等语义（同键不同载荷必须拒绝，
-- 不得静默复用）。`request_hash` = 规范化（topic + instructions + profile）的 SHA-256；
-- 旧行（NULL）按遗留口径放行（不追溯拒绝）。
--
-- 注意：执行器 tools/migrate.sh 已用 --single-transaction 包裹，本文件不得写 BEGIN/COMMIT。

ALTER TABLE runs ADD COLUMN request_hash text;

COMMENT ON COLUMN runs.request_hash IS
    'P1-1 幂等请求指纹（topic+instructions+profile 的 SHA-256）；同键不同指纹 ⇒ 409 idempotency_conflict';
