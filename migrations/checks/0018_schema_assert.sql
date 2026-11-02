-- 0018 结构自检：任务列表管理（pg_trgm 搜索 / 置顶 / 软归档，需求 22）。
--
-- 用法（CI infra job 与本地验证共用）：
--   docker compose -f docker-compose.staging.yml exec -T postgres \
--     psql -U deepresearch -d deepresearch -v ON_ERROR_STOP=1 -f - < migrations/checks/0018_schema_assert.sql
--
-- 全部写入都在同一事务内并最终 ROLLBACK：只验行为，不落数据。

BEGIN;

-- 1) pg_trgm 扩展必须存在（runs.topic ILIKE 模糊搜索的索引依赖）
DO $$
DECLARE
    n integer;
BEGIN
    SELECT count(*) INTO n FROM pg_extension WHERE extname = 'pg_trgm';
    IF n <> 1 THEN
        RAISE EXCEPTION 'schema assert: pg_trgm extension missing';
    END IF;
END $$;

-- 2) 两列必须存在、类型正确且可空（历史数据无需回填）
DO $$
DECLARE
    bad text;
BEGIN
    SELECT string_agg(c, ', ') INTO bad
    FROM (VALUES ('runs.pinned_at'), ('runs.archived_at')) AS v(c)
    WHERE NOT EXISTS (
        SELECT 1 FROM information_schema.columns col
        WHERE col.table_schema = 'public'
          AND col.table_name = split_part(v.c, '.', 1)
          AND col.column_name = split_part(v.c, '.', 2)
          AND col.is_nullable = 'YES'
          AND col.data_type = 'timestamp with time zone'
    );
    IF bad IS NOT NULL THEN
        RAISE EXCEPTION 'schema assert: columns missing / not nullable timestamptz -> %', bad;
    END IF;
END $$;

-- 3) topic GIN 索引必须存在且使用 gin_trgm_ops（防未来重建时退化为普通索引）
DO $$
DECLARE
    def text;
BEGIN
    SELECT pg_get_indexdef(idx.indexrelid) INTO def
    FROM pg_index idx
    JOIN pg_class cls ON cls.oid = idx.indexrelid
    JOIN pg_namespace ns ON ns.oid = cls.relnamespace
    WHERE ns.nspname = 'public' AND cls.relname = 'runs_topic_trgm_idx';
    IF def IS NULL THEN
        RAISE EXCEPTION 'schema assert: runs_topic_trgm_idx missing';
    END IF;
    IF position('gin' in lower(def)) = 0 OR position('gin_trgm_ops' in def) = 0 THEN
        RAISE EXCEPTION 'schema assert: runs_topic_trgm_idx is not a gin_trgm_ops index -> %', def;
    END IF;
END $$;

-- 4) 行为：默认 NULL（未置顶 / 未归档），写入后读回一致
INSERT INTO runs (run_id, topic) VALUES ('__assert_0018_run__', '__assert_0018_topic__');

DO $$
DECLARE
    n integer;
BEGIN
    SELECT count(*) INTO n FROM runs
     WHERE run_id = '__assert_0018_run__'
       AND pinned_at IS NULL AND archived_at IS NULL;
    IF n <> 1 THEN
        RAISE EXCEPTION 'schema assert: pinned_at/archived_at default is not NULL (rows=%)', n;
    END IF;
END $$;

UPDATE runs SET pinned_at = now(), archived_at = now()
 WHERE run_id = '__assert_0018_run__';

DO $$
DECLARE
    n integer;
BEGIN
    SELECT count(*) INTO n FROM runs
     WHERE run_id = '__assert_0018_run__'
       AND pinned_at IS NOT NULL AND archived_at IS NOT NULL;
    IF n <> 1 THEN
        RAISE EXCEPTION 'schema assert: pinned_at/archived_at not writable (rows=%)', n;
    END IF;
    -- 模糊搜索表达式必须可执行（索引存在且查询形态合法）
    PERFORM 1 FROM runs WHERE topic ILIKE '%__assert_0018_topic__%';
END $$;

ROLLBACK;
