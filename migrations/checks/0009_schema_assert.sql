-- 0009 结构自检：workers 列、status 约束与默认值。

BEGIN;

DO $$
DECLARE
    missing text;
BEGIN
    SELECT string_agg(c, ', ') INTO missing
    FROM (VALUES
        ('workers.worker_id'), ('workers.version'), ('workers.hostname'),
        ('workers.status'), ('workers.started_at'), ('workers.last_heartbeat_at'),
        ('workers.in_flight'), ('workers.current_run_id'), ('workers.stopped_at')
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
        INSERT INTO workers (worker_id, status) VALUES ('__assert_worker_bad__', 'whatever');
        RAISE EXCEPTION 'schema assert: workers.status accepted invalid value';
    EXCEPTION WHEN check_violation THEN
        NULL;
    END;
END $$;

INSERT INTO workers (worker_id, version, hostname) VALUES ('__assert_worker__', 'test', 'host');
DO $$
DECLARE
    st text;
BEGIN
    SELECT status INTO st FROM workers WHERE worker_id = '__assert_worker__';
    IF st <> 'active' THEN
        RAISE EXCEPTION 'schema assert: workers default status is % (expected active)', st;
    END IF;
END $$;

ROLLBACK;
