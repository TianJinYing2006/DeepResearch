-- 0023 结构自检：HITL 等待态与交互协议列（需求 36 Phase 1）。
--
-- 用法（CI infra job 与本地验证共用）：
--   docker compose -f docker-compose.staging.yml exec -T postgres \
--     psql -U deepresearch -d deepresearch -v ON_ERROR_STOP=1 -f - < migrations/checks/0023_schema_assert.sql
--
-- 全部写入都在同一事务内并最终 ROLLBACK：只验行为，不落数据。

BEGIN;

-- 1) 交互协议列必须存在
DO $$
DECLARE
    missing text;
BEGIN
    SELECT string_agg(c, ', ') INTO missing
    FROM (VALUES
        ('runs.awaiting_since'), ('runs.awaiting_expires_at'), ('runs.interaction_id'),
        ('runs.interaction_round'), ('runs.interaction_payload'), ('runs.interaction_answer'),
        ('runs.resumed_at')
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

-- 2) 状态机放宽：两个等待态必须被 CHECK 接受（只增不改旧值）
INSERT INTO runs (run_id, topic, status, awaiting_since, awaiting_expires_at,
                  interaction_id, interaction_round, interaction_payload)
VALUES ('__assert_await_clarify__', 't', 'AWAITING_CLARIFICATION', now(), now(),
        'itx-assert-1', 1, '{"kind": "clarification"}'::jsonb),
       ('__assert_await_plan__', 't', 'AWAITING_PLAN_APPROVAL', now(), now(),
        'itx-assert-2', 2, '{"kind": "plan_approval"}'::jsonb);

-- 3) 非法状态仍必须被拒绝（放宽未放穿）
DO $$
BEGIN
    BEGIN
        INSERT INTO runs (run_id, topic, status) VALUES ('__assert_bad_state_23__', 't', 'AWAITING_ANYTHING');
        RAISE EXCEPTION 'schema assert: runs.status accepted invalid awaiting value';
    EXCEPTION WHEN check_violation THEN
        NULL;
    END;
END $$;

-- 4) interaction_round 约束：0 必须被拒绝
DO $$
BEGIN
    BEGIN
        INSERT INTO runs (run_id, topic, status, interaction_round)
        VALUES ('__assert_bad_round__', 't', 'CREATED', 0);
        RAISE EXCEPTION 'schema assert: interaction_round accepted 0';
    EXCEPTION WHEN check_violation THEN
        NULL;
    END;
END $$;

-- 5) 等待态到期扫描 / 每用户计数的部分索引必须存在
DO $$
DECLARE
    missing text;
BEGIN
    SELECT string_agg(i, ', ') INTO missing
    FROM (VALUES
        ('runs_awaiting_expiry_idx'), ('runs_awaiting_user_idx')
    ) AS v(i)
    WHERE NOT EXISTS (
        SELECT 1 FROM pg_indexes
        WHERE schemaname = 'public' AND indexname = v.i
    );
    IF missing IS NOT NULL THEN
        RAISE EXCEPTION 'schema assert: missing indexes -> %', missing;
    END IF;
END $$;

ROLLBACK;
