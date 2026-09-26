-- 0002 结构自检：run_artifacts 的主键、覆盖更新与级联删除。
--
-- 用法与 0001 相同（psql -v ON_ERROR_STOP=1 -f -，由 CI infra job 与本地验证执行）；
-- 全部写入在事务内并最终 ROLLBACK。

BEGIN;

-- 1) 关键列必须存在
DO $$
DECLARE
    missing text;
BEGIN
    SELECT string_agg(c, ', ') INTO missing
    FROM (VALUES
        ('run_artifacts.run_id'), ('run_artifacts.kind'), ('run_artifacts.body'),
        ('run_artifacts.created_at'), ('run_artifacts.updated_at')
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

-- 2) 产物必须挂在已存在的 run 上（外键）
DO $$
BEGIN
    BEGIN
        INSERT INTO run_artifacts (run_id, kind, body)
        VALUES ('__assert_no_such_run__', 'report_md', 'x');
        RAISE EXCEPTION 'schema assert: orphan artifact accepted';
    EXCEPTION WHEN foreign_key_violation THEN
        NULL;
    END;
END $$;

-- 3) 同一 (run_id, kind) 覆盖式更新；不同 kind 并存
INSERT INTO runs (run_id, topic) VALUES ('__assert_artifact_run__', 't');
INSERT INTO run_artifacts (run_id, kind, body) VALUES ('__assert_artifact_run__', 'report_md', 'v1');
INSERT INTO run_artifacts (run_id, kind, body) VALUES ('__assert_artifact_run__', 'report_md', 'v2')
    ON CONFLICT (run_id, kind) DO UPDATE SET body = EXCLUDED.body, updated_at = now();
INSERT INTO run_artifacts (run_id, kind, body) VALUES ('__assert_artifact_run__', 'export_json', '{}');
DO $$
DECLARE
    n integer;
    v_body text;
BEGIN
    SELECT count(*) INTO n FROM run_artifacts WHERE run_id = '__assert_artifact_run__';
    IF n <> 2 THEN
        RAISE EXCEPTION 'schema assert: expected 2 artifacts, got %', n;
    END IF;
    SELECT a.body INTO v_body
      FROM run_artifacts a
     WHERE a.run_id = '__assert_artifact_run__' AND a.kind = 'report_md';
    IF v_body <> 'v2' THEN
        RAISE EXCEPTION 'schema assert: upsert did not replace body (got %)', v_body;
    END IF;
END $$;

-- 4) 删除 run 必须级联删除产物
DELETE FROM runs WHERE run_id = '__assert_artifact_run__';
DO $$
DECLARE
    n integer;
BEGIN
    SELECT count(*) INTO n FROM run_artifacts WHERE run_id = '__assert_artifact_run__';
    IF n <> 0 THEN
        RAISE EXCEPTION 'schema assert: ON DELETE CASCADE failed, % artifacts left', n;
    END IF;
END $$;

ROLLBACK;
