-- 0007_audit_logs.sql —— P1-5 安全审计日志
--
-- 口径依据：OWASP ASVS V16（Security Logging）与 docs/operations/production-readiness.md §3.4。
--
-- 设计：
-- - **append-only**：只追加，不提供更新/删除 API；应用层不暴露写接口之外的访问；
-- - **无敏感数据**：不落密码 / token / 原始 session；邮箱以 sha256 前缀伪名化；
-- - 事件最小集：认证成败、注销、改密、注销申请、CSRF/授权失败、管理动作；
-- - `actor_user_id` 无外键（用户删除后审计保留，不随 SET NULL 丢失身份线索）。
--
-- 注意：执行器 tools/migrate.sh 已用 --single-transaction 包裹，本文件不得写 BEGIN/COMMIT。

CREATE TABLE audit_logs (
    id            bigserial PRIMARY KEY,
    at            timestamptz NOT NULL DEFAULT now(),
    action        text NOT NULL,
    actor_user_id text,
    target_type   text,
    target_id     text,
    ip            text,
    user_agent    text,
    request_id    text,
    detail        jsonb NOT NULL DEFAULT '{}'::jsonb
);

COMMENT ON TABLE audit_logs IS
    '安全审计日志（P1-5，append-only）：无密码/token/PII；事件清单见上线清单 §3.4';
CREATE INDEX audit_logs_at_idx ON audit_logs (at DESC);
CREATE INDEX audit_logs_actor_idx ON audit_logs (actor_user_id, at DESC);
CREATE INDEX audit_logs_action_idx ON audit_logs (action, at DESC);
