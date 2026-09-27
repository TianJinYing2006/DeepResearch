-- 0006_rag_ingestions.sql —— P0-8b 异步摄取管线
--
-- 口径依据：docs/requirements/10-l3-production.md §5.7 与 docs/legal/privacy-policy.md
-- （上传文档 90 天或主动删除时清理）。
--
-- 设计（业内实践：上传登记 + 隔离区 + 状态机 + 有限重试）：
-- - 上传只做「登记 + 流式落盘到隔离区」，由 Worker 异步解析 / embedding / 写 Qdrant；
-- - 状态机：pending → processing → ready | rejected | deleted（幂等可重放）；
-- - `doc_id` 内容寻址（user:sha256[:16]）⇒ 相同内容重复上传命中既有 ready 记录，不重复计费；
-- - 租约 + 指数退避；解析类错误（超限/格式）直接 rejected，供应商类错误才重试；
-- - 隔离区文件在 ready/rejected/deleted 后删除；账号注销与 90 天保留期都会清理。
--
-- 注意：执行器 tools/migrate.sh 已用 --single-transaction 包裹，本文件不得写 BEGIN/COMMIT。

CREATE TABLE rag_ingestions (
    ingestion_id    text PRIMARY KEY,
    doc_id          text NOT NULL,
    user_id         text,
    source          text NOT NULL,
    sha256          text NOT NULL,
    size_bytes      bigint NOT NULL CHECK (size_bytes >= 0),
    stored_name     text NOT NULL,
    status          text NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'processing', 'ready', 'rejected', 'deleted')),
    chunks          integer NOT NULL DEFAULT 0 CHECK (chunks >= 0),
    attempts        integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    next_attempt_at timestamptz NOT NULL DEFAULT now(),
    lease_expires_at timestamptz,
    claimed_by      text,
    scan_status     text NOT NULL DEFAULT 'skipped'
                    CHECK (scan_status IN ('skipped', 'clean', 'infected')),
    last_error      text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    processed_at    timestamptz
);

COMMENT ON TABLE rag_ingestions IS
    'RAG 上传摄取台账（P0-8b）：登记 / 隔离区文件 / 状态机 / 重试；异步管线的事实来源';
CREATE INDEX rag_ingestions_user_idx ON rag_ingestions (user_id, created_at DESC);
CREATE INDEX rag_ingestions_due_idx ON rag_ingestions (next_attempt_at)
    WHERE status IN ('pending', 'processing');
CREATE INDEX rag_ingestions_doc_idx ON rag_ingestions (doc_id);
CREATE INDEX rag_ingestions_retention_idx ON rag_ingestions (created_at)
    WHERE status = 'ready';
