-- 需求 25（批次 A4）：错误追踪 + 反馈入口 + 帮助/合规页
--
-- 1) user_feedback —— 站内产品反馈台账（登录用户；分类/正文/可选联系方式/页面/request_id/状态）
--    - 与 moderation_appeals（内容审核申诉）语义不同：这里是通用产品反馈，人工跟进、不改审核状态；
--    - user_id 无外键：用户删除后保留线索（与 audit_logs 口径一致）；message 为唯一正文来源。
-- 2) user_consents —— 注册同意留档（PIPL 可举证：doc_type / version / agreed_at / ip_hash）
--    - version = sha256(法律文档内容)[:12]；注册事务内写入 terms/privacy 两行；
--    - 主键 (user_id, doc_type)：每用户每文档保留首次同意版本（更新协议后重新同意时覆盖为最新）。
--
-- 约定：只前向、幂等可重跑（IF NOT EXISTS）；文件内不写 BEGIN/COMMIT（执行器 --single-transaction）。

CREATE TABLE IF NOT EXISTS user_feedback (
    feedback_id text PRIMARY KEY,
    user_id     text NOT NULL,
    category    text NOT NULL CHECK (category IN ('bug', 'idea', 'other')),
    message     text NOT NULL,
    contact     text,
    page        text,
    request_id  text,
    status      text NOT NULL DEFAULT 'new' CHECK (status IN ('new', 'reviewing', 'closed')),
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS user_feedback_user_idx
    ON user_feedback (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS user_feedback_status_idx
    ON user_feedback (status, created_at DESC);

COMMENT ON TABLE user_feedback IS
    '需求 25：站内产品反馈（登录用户提交；管理端 feedback-list 跟进；不含内容审核语义）';
COMMENT ON COLUMN user_feedback.request_id IS '与 request-id 中间件串联，便于按请求回溯（不含正文）';

CREATE TABLE IF NOT EXISTS user_consents (
    user_id    text NOT NULL,
    doc_type   text NOT NULL CHECK (doc_type IN ('terms', 'privacy')),
    version    text NOT NULL,
    agreed_at  timestamptz NOT NULL DEFAULT now(),
    ip_hash    text,
    PRIMARY KEY (user_id, doc_type)
);

COMMENT ON TABLE user_consents IS
    '需求 25：注册同意留档（doc_type=terms/privacy；version=文档内容 sha256[:12]；ip_hash 只存摘要）';
