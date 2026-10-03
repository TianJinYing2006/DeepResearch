-- 0019_rag_three_layers.sql —— 需求 23：三层数据（解析快照 / 分块产物 / 向量索引）+ 版本化重建
--
-- 背景（需求 23 §3）：
--   原设计「只存 chunks、重索引不重解析」与分块器演进冲突——分块产物丢失标题/表格/页序，
--   无法可靠重分块。本迁移引入三层数据：
--     1) rag_parse_snapshots：结构化解析快照（分块器升级的唯一输入）；
--     2) rag_chunks：分块产物（含 generation / chunk_id / locator）；
--     3) Qdrant：派生物（可随时从 1)+2) 重建）；
--   以及 rag_index_generations 版本状态机（building → active → retired；失败留旧版本，
--   检索永无「不可用窗口」）与全局索引修订号（缓存失效依据）。
--
-- 兼容性：全部为新增表/新增列（旧代码忽略新表；Qdrant 旧点无 generation/active 字段，
--   检索侧以「active != false」语义放行，见 retriever）。
-- 注意：执行器 tools/migrate.sh 已用 --single-transaction 包裹，本文件不得写 BEGIN/COMMIT。

-- ① 解析快照：有序结构块（标题路径 / 定位 / 文本；表结构以 TSV 行编码在 text 中）
CREATE TABLE IF NOT EXISTS rag_parse_snapshots (
    doc_id      text        NOT NULL,
    block_index int         NOT NULL,
    kind        text        NOT NULL,  -- heading | paragraph | table | code | slide | sheet
    title_path  text[]      NOT NULL DEFAULT '{}',
    locator     jsonb       NOT NULL DEFAULT '{}'::jsonb,  -- {page|slide|sheet|row_range|href}
    text        text        NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (doc_id, block_index)
);

-- ② 分块产物：某个 generation 的块（含身份与定位；embedding 输入与展示正文分离）
CREATE TABLE IF NOT EXISTS rag_chunks (
    doc_id      text        NOT NULL,
    generation  int         NOT NULL,
    chunk_index int         NOT NULL,
    chunk_id    text        NOT NULL,
    text        text        NOT NULL,  -- 展示正文（不含标题路径）
    embed_text  text        NOT NULL,  -- embedding 输入（标题路径 + 正文）
    title_path  text[]      NOT NULL DEFAULT '{}',
    locator     jsonb       NOT NULL DEFAULT '{}'::jsonb,
    created_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (doc_id, generation, chunk_index),
    UNIQUE (chunk_id)
);

-- ③ 版本状态机：同一 doc 至多一个 active；building 失败不影响旧 active
CREATE TABLE IF NOT EXISTS rag_index_generations (
    id              bigserial   PRIMARY KEY,
    doc_id          text        NOT NULL,
    generation      int         NOT NULL,
    chunker_version text        NOT NULL,
    embedding_model text        NOT NULL,
    embedding_dim   int         NOT NULL,
    status          text        NOT NULL DEFAULT 'building'
                    CHECK (status IN ('building', 'active', 'retired', 'failed')),
    chunk_count     int         NOT NULL DEFAULT 0,
    error           text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    activated_at    timestamptz,
    UNIQUE (doc_id, generation)
);

CREATE UNIQUE INDEX IF NOT EXISTS rag_index_generations_active_uk
    ON rag_index_generations (doc_id) WHERE status = 'active';
CREATE INDEX IF NOT EXISTS rag_index_generations_doc_idx
    ON rag_index_generations (doc_id, generation DESC);

-- ④ 摄取台账扩列：展示名 / 标签 / 活动版本 / 本地修订号（列表与缓存可见性）/ 任务类型
ALTER TABLE rag_ingestions ADD COLUMN IF NOT EXISTS display_name      text;
ALTER TABLE rag_ingestions ADD COLUMN IF NOT EXISTS tags              jsonb NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE rag_ingestions ADD COLUMN IF NOT EXISTS active_generation int;
ALTER TABLE rag_ingestions ADD COLUMN IF NOT EXISTS index_revision    bigint NOT NULL DEFAULT 0;
ALTER TABLE rag_ingestions ADD COLUMN IF NOT EXISTS task              text NOT NULL DEFAULT 'ingest';

-- ⑤ 全局索引修订号（单行）：任何写路径（上传完成 / 删除 / 版本切换）递增，
--    供检索缓存（BM25 语料等）按键失效 —— 「删除后不可从缓存命中」的实现依据。
CREATE TABLE IF NOT EXISTS rag_revision (
    id       int    PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    revision bigint NOT NULL DEFAULT 0
);

INSERT INTO rag_revision (id, revision) VALUES (1, 0) ON CONFLICT (id) DO NOTHING;

COMMENT ON TABLE rag_parse_snapshots IS '需求 23：结构化解析快照（重新分块的唯一输入）';
COMMENT ON TABLE rag_chunks IS '需求 23：分块产物（generation + chunk_id + locator；预览/引用/re-embed 输入）';
COMMENT ON TABLE rag_index_generations IS '需求 23：版本状态机（building→active→retired；同 doc 单 active）';
COMMENT ON TABLE rag_revision IS '需求 23：全局索引修订号（缓存失效依据）';
