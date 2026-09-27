-- 0011 结构自检：usage_ledger 列与 kind / cost_source 约束。

BEGIN;

DO $$
DECLARE
    missing text;
BEGIN
    SELECT string_agg(c, ', ') INTO missing
    FROM (VALUES
        ('usage_ledger.run_id'), ('usage_ledger.attempt'), ('usage_ledger.kind'),
        ('usage_ledger.provider'), ('usage_ledger.model'), ('usage_ledger.role'),
        ('usage_ledger.input_tokens'), ('usage_ledger.output_tokens'),
        ('usage_ledger.total_tokens'), ('usage_ledger.cost_estimate_cny'),
        ('usage_ledger.cost_source'), ('usage_ledger.request_id'), ('usage_ledger.detail')
    ) AS v(c)
    WHERE NOT EXISTS (
        SELECT 1 FROM information_schema.columns col
        WHERE col.table_schema = 'public'
          AND col.table_name = split_part(v.c, '.', 1)
          AND col.column_name = split_part(v.c, '.', 2)
    );
    IF missing IS NOT NULL THEN
        RAISE EXCEPTION 'schema assert: missing columns -> %', missing;
    END IF;
END $$;

DO $$
BEGIN
    BEGIN
        INSERT INTO usage_ledger (kind) VALUES ('whatever');
        RAISE EXCEPTION 'schema assert: usage_ledger.kind accepted invalid value';
    EXCEPTION WHEN check_violation THEN
        NULL;
    END;
    BEGIN
        INSERT INTO usage_ledger (kind, cost_source) VALUES ('llm', 'whatever');
        RAISE EXCEPTION 'schema assert: usage_ledger.cost_source accepted invalid value';
    EXCEPTION WHEN check_violation THEN
        NULL;
    END;
END $$;

INSERT INTO usage_ledger (run_id, attempt, kind, provider, model, total_tokens, cost_source)
VALUES ('__assert_usage_run__', 1, 'llm', 'dashscope', 'qwen-plus', 100, 'estimate');
DO $$
DECLARE
    n integer;
BEGIN
    SELECT count(*) INTO n FROM usage_ledger WHERE run_id = '__assert_usage_run__';
    IF n <> 1 THEN
        RAISE EXCEPTION 'schema assert: usage_ledger insert failed';
    END IF;
END $$;

ROLLBACK;
