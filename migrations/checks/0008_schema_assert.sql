-- 0008 结构自检：runs.request_hash 列存在且可写入。

BEGIN;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'runs'
          AND column_name = 'request_hash'
    ) THEN
        RAISE EXCEPTION 'schema assert: runs.request_hash column missing';
    END IF;
END $$;

INSERT INTO runs (run_id, topic, idempotency_key, request_hash)
VALUES ('__assert_hash_run__', 't', 'k-hash-assert', 'abc123')
ON CONFLICT (run_id) DO NOTHING;
DO $$
DECLARE
    h text;
BEGIN
    SELECT request_hash INTO h FROM runs WHERE run_id = '__assert_hash_run__';
    IF h <> 'abc123' THEN
        RAISE EXCEPTION 'schema assert: runs.request_hash not persisted (got %)', h;
    END IF;
END $$;

ROLLBACK;
