-- 0031 结构自检：schema_migrations 内容指纹列（需求 42 #339）。
--
-- 用法（CI infra job 与本地验证共用）：
--   docker compose -f docker-compose.staging.yml exec -T postgres \
--     psql -U deepresearch -d deepresearch -v ON_ERROR_STOP=1 -f - < migrations/checks/0031_schema_assert.sql
--
-- 全部写入都在同一事务内并最终 ROLLBACK：只验行为，不落数据。

BEGIN;

-- 1) sha256 列必须存在且可空（历史行 NULL；新行由 migrate.sh 写入 64 位 hex）
DO $$
DECLARE
    bad text;
BEGIN
    SELECT string_agg(c, ', ') INTO bad
    FROM (VALUES ('schema_migrations.sha256')) AS v(c)
    WHERE NOT EXISTS (
        SELECT 1 FROM information_schema.columns col
        WHERE col.table_schema = 'public'
          AND col.table_name = split_part(v.c, '.', 1)
          AND col.column_name = split_part(v.c, '.', 2)
          AND col.is_nullable = 'YES'
          AND col.data_type = 'text'
    );
    IF bad IS NOT NULL THEN
        RAISE EXCEPTION 'schema assert: columns missing / not nullable text -> %', bad;
    END IF;
END $$;

-- 2) 记录写入 + 读回：版本仍是主键，指纹可为 NULL（历史行回填前）
INSERT INTO schema_migrations (version, sha256)
VALUES ('__assert_0031_hash__', repeat('a', 64));

DO $$
DECLARE
    stored text;
BEGIN
    SELECT sha256 INTO stored FROM schema_migrations WHERE version = '__assert_0031_hash__';
    IF stored IS NULL OR length(stored) <> 64 THEN
        RAISE EXCEPTION 'schema assert: schema_migrations.sha256 not persisted (value=%)', stored;
    END IF;

    BEGIN
        INSERT INTO schema_migrations (version, sha256)
        VALUES ('__assert_0031_hash__', repeat('b', 64));
        RAISE EXCEPTION 'schema assert: schema_migrations accepted duplicate version';
    EXCEPTION WHEN unique_violation THEN
        NULL;
    END;

    -- 历史行：NULL 指纹必须允许（否则现有库无法平滑升级）
    INSERT INTO schema_migrations (version, sha256)
    VALUES ('__assert_0031_legacy__', NULL);
END $$;

-- 3) 指纹可被更新（migrate.sh 的历史行回填路径）
UPDATE schema_migrations SET sha256 = repeat('c', 64)
 WHERE version = '__assert_0031_legacy__';

DO $$
DECLARE
    stored text;
BEGIN
    SELECT sha256 INTO stored FROM schema_migrations WHERE version = '__assert_0031_legacy__';
    IF stored IS NULL OR stored <> repeat('c', 64) THEN
        RAISE EXCEPTION 'schema assert: schema_migrations.sha256 backfill update failed';
    END IF;
END $$;

ROLLBACK;
