-- 0025 结构自检：export_jobs（需求 38 F2）。
--
-- 用法（CI infra job 与本地验证共用）：
--   docker compose -f docker-compose.staging.yml exec -T postgres \
--     psql -U deepresearch -d deepresearch -v ON_ERROR_STOP=1 -f - < migrations/checks/0025_schema_assert.sql
--
-- 全部写入都在同一事务内并最终 ROLLBACK：只验行为，不落数据。

BEGIN;

-- 0) 造一个 run 以承载外键（assert 事务内，最终回滚）
INSERT INTO runs (run_id, topic, status) VALUES ('__assert_export_job__', 't', 'SUCCEEDED');

-- 1) 进行中同指纹唯一（QUEUED）
INSERT INTO export_jobs (job_id, run_id, request_hash)
VALUES ('__assert_job_1__', '__assert_export_job__', '__assert_hash__');

DO $$
BEGIN
    BEGIN
        INSERT INTO export_jobs (job_id, run_id, request_hash)
        VALUES ('__assert_job_2__', '__assert_export_job__', '__assert_hash__');
        RAISE EXCEPTION 'schema assert: duplicate active request_hash accepted';
    EXCEPTION WHEN unique_violation THEN
        NULL;
    END;
END $$;

-- 2) 终态不占用唯一键（同指纹可在首个作业 FAILED 后再建）
UPDATE export_jobs SET status = 'FAILED', finished_at = now() WHERE job_id = '__assert_job_1__';
INSERT INTO export_jobs (job_id, run_id, request_hash)
VALUES ('__assert_job_3__', '__assert_export_job__', '__assert_hash__');

-- 3) 状态白名单：非法值必须被拒绝
DO $$
BEGIN
    BEGIN
        INSERT INTO export_jobs (job_id, run_id, request_hash, status)
        VALUES ('__assert_job_bad__', '__assert_export_job__', '__assert_hash_bad__', 'WEIRD');
        RAISE EXCEPTION 'schema assert: export_jobs.status accepted invalid value';
    EXCEPTION WHEN check_violation THEN
        NULL;
    END;
END $$;

-- 4) 关键列必须存在（防未来重建丢列）
DO $$
DECLARE
    missing text;
BEGIN
    SELECT string_agg(c, ', ') INTO missing
    FROM (VALUES
        ('export_jobs.job_id'), ('export_jobs.run_id'), ('export_jobs.user_id'),
        ('export_jobs.kind'), ('export_jobs.status'), ('export_jobs.attempt'),
        ('export_jobs.max_attempts'), ('export_jobs.request_hash'),
        ('export_jobs.lease_owner'), ('export_jobs.lease_expires_at'),
        ('export_jobs.error_code'), ('export_jobs.error_detail'),
        ('export_jobs.result_key'), ('export_jobs.result_bytes'),
        ('export_jobs.result_hash'), ('export_jobs.result_expires_at'),
        ('export_jobs.created_at'), ('export_jobs.started_at'), ('export_jobs.finished_at')
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

-- 5) 外键与部分唯一索引必须存在
DO $$
DECLARE
    missing text;
BEGIN
    SELECT string_agg(i, ', ') INTO missing
    FROM (VALUES
        ('export_jobs_active_uk'), ('export_jobs_claim_idx'),
        ('export_jobs_lease_idx'), ('export_jobs_run_idx')
    ) AS v(i)
    WHERE NOT EXISTS (
        SELECT 1 FROM pg_indexes
        WHERE schemaname = 'public' AND indexname = v.i
    );
    IF missing IS NOT NULL THEN
        RAISE EXCEPTION 'schema assert: missing indexes -> %', missing;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'export_jobs_run_id_fkey' AND contype = 'f'
    ) THEN
        RAISE EXCEPTION 'schema assert: export_jobs_run_id_fkey missing';
    END IF;
END $$;

ROLLBACK;
