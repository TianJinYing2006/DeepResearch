-- 0011_usage_ledger.sql —— P1-4 逐调用用量账本
--
-- 口径依据：LLM 成本归因实践（逐调用行 + attempt + cost_source；先对请求数再对钱；
-- 账本不含 prompt / PII）与 docs/operations/production-readiness.md §3.7。
--
-- 设计：
-- - 每条外部调用一行：LLM / embedding / 搜索（重试每次也记，attempt 可区分）；
-- - `cost_source`：estimate（本地价格表估算）/ per_call（按次计费未建模）/ provider（供应商实报）；
-- - `run_id` 无外键（审计保留；测试清理 runs 不级联账本）；
-- - 对账命令：CLI `usage-summary`；与供应商账单核对流程见上线清单 §3.7。
--
-- 注意：执行器 tools/migrate.sh 已用 --single-transaction 包裹，本文件不得写 BEGIN/COMMIT。

CREATE TABLE usage_ledger (
    id                bigserial PRIMARY KEY,
    run_id            text,
    attempt           integer NOT NULL DEFAULT 1 CHECK (attempt >= 1),
    kind              text NOT NULL CHECK (kind IN ('llm', 'embedding', 'search')),
    provider          text NOT NULL DEFAULT '',
    model             text NOT NULL DEFAULT '',
    role              text NOT NULL DEFAULT '',
    input_tokens      integer NOT NULL DEFAULT 0 CHECK (input_tokens >= 0),
    output_tokens     integer NOT NULL DEFAULT 0 CHECK (output_tokens >= 0),
    total_tokens      integer NOT NULL DEFAULT 0 CHECK (total_tokens >= 0),
    cost_estimate_cny numeric(12,6) NOT NULL DEFAULT 0 CHECK (cost_estimate_cny >= 0),
    cost_source       text NOT NULL DEFAULT 'estimate'
                      CHECK (cost_source IN ('estimate', 'per_call', 'provider')),
    request_id        text,
    detail            jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at        timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE usage_ledger IS
    '逐调用用量账本（P1-4）：无 prompt/PII；重试逐次记账；cost_source 标注精度来源';
CREATE INDEX usage_ledger_run_idx ON usage_ledger (run_id, created_at);
CREATE INDEX usage_ledger_created_idx ON usage_ledger (created_at);
CREATE INDEX usage_ledger_kind_idx ON usage_ledger (kind, created_at);
