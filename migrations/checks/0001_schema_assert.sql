-- 0001 结构自检：验证 runs / run_events 的列、约束与级联行为。
--
-- 用法（CI infra job 与本地验证共用）：
--   docker compose -f docker-compose.staging.yml exec -T postgres \
--     psql -U deepresearch -d deepresearch -v ON_ERROR_STOP=1 -f - < migrations/checks/0001_schema_assert.sql
--
-- 全部写入都在同一事务内并最终 ROLLBACK：只验行为，不落数据。

BEGIN;

-- 1) 关键列必须存在
DO $$
DECLARE
    missing text;
BEGIN
    SELECT string_agg(c, ', ') INTO missing
    FROM (VALUES
        ('runs.run_id'), ('runs.status'), ('runs.research_status'), ('runs.stop_reason'),
        ('runs.current_node'), ('runs.cancel_requested_at'), ('runs.timeout_at'),
        ('runs.hard_deadline_at'), ('runs.budget_limit_cny'), ('runs.budget_used_cny'),
        ('runs.idempotency_key'), ('runs.worker_id'), ('runs.lease_expires_at'),
        ('run_events.run_id'), ('run_events.sequence'), ('run_events.event_id'),
        ('run_events.event_type'), ('run_events.payload')
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

-- 2) 状态机：非法状态必须被 CHECK 拒绝
DO $$
BEGIN
    BEGIN
        INSERT INTO runs (run_id, topic, status) VALUES ('__assert_bad_state__', 't', 'NOT_A_STATE');
        RAISE EXCEPTION 'schema assert: runs.status accepted invalid value';
    EXCEPTION WHEN check_violation THEN
        NULL;
    END;
END $$;

-- 3) 创建幂等：同 (user_id, idempotency_key) 第二次必须被部分唯一索引拒绝
INSERT INTO runs (run_id, topic, user_id, idempotency_key)
VALUES ('__assert_idem_a__', 't', 'u1', 'k1');
DO $$
BEGIN
    BEGIN
        INSERT INTO runs (run_id, topic, user_id, idempotency_key)
        VALUES ('__assert_idem_b__', 't', 'u1', 'k1');
        RAISE EXCEPTION 'schema assert: duplicate idempotency_key accepted';
    EXCEPTION WHEN unique_violation THEN
        NULL;
    END;
END $$;

-- 4) 幂等键为 NULL 的行不受唯一索引约束（可并存）
INSERT INTO runs (run_id, topic, user_id, idempotency_key)
VALUES ('__assert_idem_c__', 't', 'u1', NULL), ('__assert_idem_d__', 't', 'u1', NULL);

-- 5) 事件主键 (run_id, sequence) 防重放
INSERT INTO run_events (run_id, sequence, event_type, payload)
VALUES ('__assert_idem_a__', 0, 'RUN_STARTED', '{"ok": true}'::jsonb);
DO $$
BEGIN
    BEGIN
        INSERT INTO run_events (run_id, sequence, event_type)
        VALUES ('__assert_idem_a__', 0, 'DUPLICATE');
        RAISE EXCEPTION 'schema assert: duplicate (run_id, sequence) accepted';
    EXCEPTION WHEN unique_violation THEN
        NULL;
    END;
END $$;

-- 6) 事件必须挂在已存在的 run 上（外键）
DO $$
BEGIN
    BEGIN
        INSERT INTO run_events (run_id, sequence, event_type)
        VALUES ('__assert_no_such_run__', 0, 'ORPHAN');
        RAISE EXCEPTION 'schema assert: orphan run_event accepted';
    EXCEPTION WHEN foreign_key_violation THEN
        NULL;
    END;
END $$;

-- 7) 删除 run 必须级联删除其事件
DELETE FROM runs WHERE run_id = '__assert_idem_a__';
DO $$
DECLARE
    n integer;
BEGIN
    SELECT count(*) INTO n FROM run_events WHERE run_id = '__assert_idem_a__';
    IF n <> 0 THEN
        RAISE EXCEPTION 'schema assert: ON DELETE CASCADE failed, % events left', n;
    END IF;
END $$;

ROLLBACK;
