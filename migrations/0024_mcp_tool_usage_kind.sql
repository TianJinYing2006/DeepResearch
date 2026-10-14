-- 0024_mcp_tool_usage_kind.sql —— 需求 37 M1：usage_ledger.kind 扩 'tool'（2026-10-14）
--
-- 设计（TBD-4 拍板）：
--   - MCP 工具调用计次入账：kind='tool'（provider=server 名、role=工具名）；
--   - 成本口径不变：per_call 计次、金额 0（仅服务端有可信价格时才写金额，见 web/backend/usage.py）；
--   - 只扩 CHECK 白名单，既有行与旧三值语义不变（向前兼容）。
-- 注意：执行器 tools/migrate.sh 已用 --single-transaction 包裹，本文件不得写 BEGIN/COMMIT。

ALTER TABLE usage_ledger DROP CONSTRAINT usage_ledger_kind_check;
ALTER TABLE usage_ledger ADD CONSTRAINT usage_ledger_kind_check
    CHECK (kind IN ('llm', 'embedding', 'search', 'tool'));
