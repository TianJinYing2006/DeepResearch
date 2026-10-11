-- 0022_snapshots.sql —— 需求 34 E1：网页快照落盘（TBD-1 拍板口径，2026-10-11）
--
-- 两表设计（需求 34 §5 TBD-1）：
--   snapshots     全局内容寻址（sha256 主键）存一份；同文（同 extractor_version）跨 run 去重；
--   run_snapshots run ↔ 快照 关联（run 内去重）；删除 run 级联解除关联；
--                 本体清理靠保留期任务（无引用 + 超期），不靠外键级联（TBD-1 口径）。
-- 文本上限（64KB）与截断标记（text_truncated）由应用层控制（research_engine/net/webtext.py）。
-- 注意：执行器 tools/migrate.sh 已用 --single-transaction 包裹，本文件不得写 BEGIN/COMMIT。

CREATE TABLE IF NOT EXISTS snapshots (
    sha256            text PRIMARY KEY,
    url               text NOT NULL,
    content_type      text NOT NULL DEFAULT '',
    source_bytes      integer NOT NULL DEFAULT 0 CHECK (source_bytes >= 0),
    text              text NOT NULL,
    text_truncated    boolean NOT NULL DEFAULT false,
    extractor_version text NOT NULL,
    created_at        timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS run_snapshots (
    run_id     text NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    sha256     text NOT NULL REFERENCES snapshots(sha256) ON DELETE CASCADE,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id, sha256)
);

CREATE INDEX IF NOT EXISTS idx_run_snapshots_run ON run_snapshots (run_id);
CREATE INDEX IF NOT EXISTS idx_snapshots_created_at ON snapshots (created_at);
