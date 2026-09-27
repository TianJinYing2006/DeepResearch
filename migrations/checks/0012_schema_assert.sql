-- 0012 结构自检：run_artifacts 对象存储元数据列与约束。

BEGIN;

DO $$
DECLARE
    missing text;
BEGIN
    SELECT string_agg(c, ', ') INTO missing
    FROM (VALUES
        ('run_artifacts.storage'), ('run_artifacts.object_key'),
        ('run_artifacts.sha256'), ('run_artifacts.size_bytes')
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
        INSERT INTO runs (run_id, topic) VALUES ('__assert_artifact_run__', 't')
        ON CONFLICT DO NOTHING;
        INSERT INTO run_artifacts (run_id, kind, body, storage)
        VALUES ('__assert_artifact_run__', 'bad', '', 'whatever');
        RAISE EXCEPTION 'schema assert: run_artifacts.storage accepted invalid value';
    EXCEPTION WHEN check_violation THEN
        NULL;
    END;
END $$;

INSERT INTO run_artifacts (run_id, kind, body, storage, object_key, sha256, size_bytes)
VALUES ('__assert_artifact_run__', 'report_md', '', 's3', 'runs/x/report_md', 'abc', 3)
ON CONFLICT (run_id, kind) DO NOTHING;
DO $$
DECLARE
    n integer;
BEGIN
    SELECT count(*) INTO n FROM run_artifacts
     WHERE run_id = '__assert_artifact_run__' AND storage = 's3' AND object_key IS NOT NULL;
    IF n < 1 THEN
        RAISE EXCEPTION 'schema assert: s3 artifact metadata not persisted';
    END IF;
END $$;

ROLLBACK;
