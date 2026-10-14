-- 0024 结构自检：usage_ledger.kind 扩展 'tool'（需求 37 M1）。
--
-- 用法（CI infra job 与本地验证共用）：
--   docker compose -f docker-compose.staging.yml exec -T postgres \
--     psql -U deepresearch -d deepresearch -v ON_ERROR_STOP=1 -f - < migrations/checks/0024_schema_assert.sql
--
-- 全部写入都在同一事务内并最终 ROLLBACK：只验行为，不落数据。

BEGIN;

-- 1) 'tool' 必须被接受（MCP 工具计次入账）
INSERT INTO usage_ledger (run_id, kind, provider, role)
VALUES ('__assert_mcp_tool__', 'tool', 'mcp-assert', 'search');

-- 2) 非法 kind 仍必须被拒绝（白名单未被放空）
DO $$
BEGIN
    BEGIN
        INSERT INTO usage_ledger (run_id, kind) VALUES ('__assert_bad_kind__', 'weird_kind');
        RAISE EXCEPTION 'schema assert: usage_ledger.kind accepted invalid value';
    EXCEPTION WHEN check_violation THEN
        NULL;
    END;
END $$;

-- 3) 约束本体必须存在且包含 'tool'（防止未来重建时丢白名单）
DO $$
DECLARE
    def text;
BEGIN
    SELECT pg_get_constraintdef(oid) INTO def
    FROM pg_constraint WHERE conname = 'usage_ledger_kind_check';
    IF def IS NULL THEN
        RAISE EXCEPTION 'schema assert: usage_ledger_kind_check missing';
    END IF;
    IF position('tool' in def) = 0 THEN
        RAISE EXCEPTION 'schema assert: usage_ledger_kind_check lacks tool -> %', def;
    END IF;
END $$;

ROLLBACK;
