-- 0029 结构自检：报告分享「一 run 单活跃」唯一部分索引（需求 26 / #330）。
--
-- 用法（CI infra job 与本地验证共用）：
--   docker compose -f docker-compose.staging.yml exec -T postgres \
--     psql -U deepresearch -d deepresearch -v ON_ERROR_STOP=1 -f - < migrations/checks/0029_schema_assert.sql
--
-- 全部写入都在同一事务内并最终 ROLLBACK：只验行为，不落数据。

BEGIN;

-- 0) 造两个 run 以承载外键（assert 事务内，最终回滚）
INSERT INTO runs (run_id, topic, status) VALUES ('__assert_share_run__', 't', 'SUCCEEDED');
INSERT INTO runs (run_id, topic, status) VALUES ('__assert_share_run2__', 't', 'SUCCEEDED');

-- 1) 唯一部分索引必须存在，且带 UNIQUE + WHERE revoked_at IS NULL
DO $$
DECLARE
    def text;
BEGIN
    SELECT indexdef INTO def
      FROM pg_indexes
     WHERE schemaname = 'public' AND indexname = 'report_shares_run_active_uk';
    IF def IS NULL THEN
        RAISE EXCEPTION 'schema assert: report_shares_run_active_uk missing';
    END IF;
    IF position('UNIQUE' in def) = 0 THEN
        RAISE EXCEPTION 'schema assert: report_shares_run_active_uk is not UNIQUE -> %', def;
    END IF;
    IF position('revoked_at IS NULL' in def) = 0 THEN
        RAISE EXCEPTION 'schema assert: report_shares_run_active_uk lost partial predicate -> %', def;
    END IF;
END $$;

-- 2) 同一 run 两条活跃行必须被拒（并发创建的数据库级兜底）
INSERT INTO report_shares (share_id, token_hash, run_id, created_by)
VALUES ('__assert_share_1__', '__assert_share_tok_1__', '__assert_share_run__', '__assert_user__');

DO $$
BEGIN
    BEGIN
        INSERT INTO report_shares (share_id, token_hash, run_id, created_by)
        VALUES ('__assert_share_2__', '__assert_share_tok_2__', '__assert_share_run__',
                '__assert_user__');
        RAISE EXCEPTION 'schema assert: duplicate active share accepted';
    EXCEPTION WHEN unique_violation THEN
        NULL;
    END;
END $$;

-- 3) 撤销后不占用唯一位（「重新生成」= 撤销旧 + 插入新）
UPDATE report_shares SET revoked_at = now() WHERE share_id = '__assert_share_1__';
INSERT INTO report_shares (share_id, token_hash, run_id, created_by)
VALUES ('__assert_share_3__', '__assert_share_tok_3__', '__assert_share_run__', '__assert_user__');

-- 4) 索引按 run 生效：另一 run 的活跃分享不受影响
INSERT INTO report_shares (share_id, token_hash, run_id, created_by)
VALUES ('__assert_share_4__', '__assert_share_tok_4__', '__assert_share_run2__', '__assert_user__');

ROLLBACK;
