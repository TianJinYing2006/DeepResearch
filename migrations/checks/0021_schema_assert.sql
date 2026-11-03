-- 0021 结构自检：报告只读分享（token 只存 hash / run 级联 / 可过期，需求 26 批次 A5）。
--
-- 用法（CI infra job 与本地验证共用）：
--   docker compose -f docker-compose.staging.yml exec -T postgres \
--     psql -U deepresearch -d deepresearch -v ON_ERROR_STOP=1 -f - < migrations/checks/0021_schema_assert.sql
--
-- 全部写入都在同一事务内并最终 ROLLBACK：只验行为，不落数据。

BEGIN;

-- 0) 造一个 run 以承载外键（assert 事务内，最终回滚）
INSERT INTO runs (run_id, topic) VALUES ('__assert_0021_run__', 't');

-- 1) token 摘要唯一且必填；expires_at 可空（NULL = 永不过期）
INSERT INTO report_shares (share_id, token_hash, run_id, created_by)
VALUES ('__assert_0021_share__', '__assert_0021_token__', '__assert_0021_run__', '__assert_0021_user__');

INSERT INTO report_shares (share_id, token_hash, run_id, created_by, expires_at, revoked_at)
VALUES ('__assert_0021_share2__', '__assert_0021_token2__', '__assert_0021_run__',
        '__assert_0021_user__', now() + interval '30 days', now());
-- ↑ 设为已撤销：迁移 0029 起「一 run 一条活跃链接」（report_shares_run_active_uk 唯一部分索引），
--   本行仅验证 expires_at 可赋非空值，不应再制造第二条活跃链接（合并态兼容，见需求 42 #329）。

DO $$
DECLARE
    n integer;
BEGIN
    SELECT count(*) INTO n FROM report_shares
     WHERE share_id = '__assert_0021_share__'
       AND expires_at IS NULL AND revoked_at IS NULL AND access_count = 0;
    IF n <> 1 THEN
        RAISE EXCEPTION 'schema assert: report_shares expires_at NULL / defaults broken (rows=%)', n;
    END IF;
END $$;

DO $$
BEGIN
    BEGIN
        INSERT INTO report_shares (share_id, token_hash, run_id, created_by, revoked_at)
        VALUES ('__assert_0021_share_dup_token__', '__assert_0021_token__',
                '__assert_0021_run__', '__assert_0021_user__', now());
        RAISE EXCEPTION 'schema assert: report_shares accepted duplicate token_hash';
    EXCEPTION WHEN unique_violation THEN
        NULL;
    END;
    BEGIN
        INSERT INTO report_shares (share_id, token_hash, run_id, created_by, revoked_at)
        VALUES ('__assert_0021_share_null_token__', NULL,
                '__assert_0021_run__', '__assert_0021_user__', now());
        RAISE EXCEPTION 'schema assert: report_shares.token_hash accepted NULL';
    EXCEPTION WHEN not_null_violation THEN
        NULL;
    END;
    BEGIN
        INSERT INTO report_shares (share_id, token_hash, run_id, created_by, revoked_at)
        VALUES ('__assert_0021_share__', '__assert_0021_token3__',
                '__assert_0021_run__', '__assert_0021_user__', now());
        RAISE EXCEPTION 'schema assert: report_shares accepted duplicate share_id';
    EXCEPTION WHEN unique_violation THEN
        NULL;
    END;
END $$;

-- 2) run 外键必须存在且 ON DELETE CASCADE（删除 run 级联失效分享）
DO $$
DECLARE
    deltype text;
BEGIN
    SELECT confdeltype::text INTO deltype
    FROM pg_constraint
    WHERE conname = 'report_shares_run_id_fkey' AND contype = 'f';
    IF deltype IS NULL THEN
        RAISE EXCEPTION 'schema assert: report_shares_run_id_fkey missing';
    END IF;
    IF deltype <> 'c' THEN
        RAISE EXCEPTION 'schema assert: report_shares.run_id FK is not ON DELETE CASCADE (confdeltype=%)', deltype;
    END IF;
END $$;

-- 3) 两个查询索引必须存在（活跃分享按 run / 过期清理按 expires_at）
DO $$
DECLARE
    missing text;
BEGIN
    SELECT string_agg(i, ', ') INTO missing
    FROM (VALUES ('report_shares_run_active_idx'), ('report_shares_expires_idx')) AS v(i)
    WHERE NOT EXISTS (
        SELECT 1 FROM pg_indexes
        WHERE schemaname = 'public' AND indexname = v.i
    );
    IF missing IS NOT NULL THEN
        RAISE EXCEPTION 'schema assert: missing indexes -> %', missing;
    END IF;
END $$;

-- 4) 行为：删除 run 后分享行必须随之消失（不残留可访问 token）
DELETE FROM runs WHERE run_id = '__assert_0021_run__';

DO $$
DECLARE
    n integer;
BEGIN
    SELECT count(*) INTO n FROM report_shares
     WHERE share_id IN ('__assert_0021_share__', '__assert_0021_share2__');
    IF n <> 0 THEN
        RAISE EXCEPTION 'schema assert: report_shares survived run deletion (cascade broken, rows=%)', n;
    END IF;
END $$;

ROLLBACK;
