-- 0028 结构自检：TOTP 防重放（需求 42 / RFC 6238 §5.2）。
--
-- 用法（CI infra job 与本地验证共用）：
--   docker compose -f docker-compose.staging.yml exec -T postgres \
--     psql -U deepresearch -d deepresearch -v ON_ERROR_STOP=1 -f - < migrations/checks/0028_schema_assert.sql
--
-- 全部写入都在同一事务内并最终 ROLLBACK：只验行为，不落数据。

BEGIN;

-- 0) 造一个用户与一个已确认因子以承载断言（assert 事务内，最终回滚）
INSERT INTO users (user_id, email, password_hash)
VALUES ('__assert_replay_user__', 'replay-assert@example.com', 'x');
INSERT INTO mfa_factors (factor_id, user_id, secret_encrypted, confirmed_at)
VALUES ('__assert_replay_factor__', '__assert_replay_user__', '\x00'::bytea, now());

-- 1) 关键列存在、类型 bigint、且可空（历史因子升级后为 NULL，首次验证即可消费）
DO $$
DECLARE
    col_type text;
    col_nullable text;
BEGIN
    SELECT data_type, is_nullable INTO col_type, col_nullable
    FROM information_schema.columns
    WHERE table_schema = 'public'
      AND table_name = 'mfa_factors'
      AND column_name = 'last_used_step';
    IF col_type IS NULL THEN
        RAISE EXCEPTION 'schema assert: mfa_factors.last_used_step missing';
    END IF;
    IF col_type <> 'bigint' THEN
        RAISE EXCEPTION 'schema assert: mfa_factors.last_used_step type is %', col_type;
    END IF;
    IF col_nullable <> 'YES' THEN
        RAISE EXCEPTION 'schema assert: mfa_factors.last_used_step must be nullable';
    END IF;
END $$;

-- 2) 原子消费谓词语义：同一时间步第二次消费必须 0 行（重放被拒），更晚的步可推进
DO $$
DECLARE
    changed integer;
    current_step bigint;
BEGIN
    -- 首次消费 step=100
    UPDATE mfa_factors SET last_used_step = 100, last_used_at = now()
    WHERE factor_id = '__assert_replay_factor__'
      AND (last_used_step IS NULL OR last_used_step < 100);
    GET DIAGNOSTICS changed = ROW_COUNT;
    IF changed <> 1 THEN
        RAISE EXCEPTION 'schema assert: first consume of step 100 affected % rows', changed;
    END IF;

    -- 同一步重放（同一 code 第二次使用）必须 0 行
    UPDATE mfa_factors SET last_used_step = 100, last_used_at = now()
    WHERE factor_id = '__assert_replay_factor__'
      AND (last_used_step IS NULL OR last_used_step < 100);
    GET DIAGNOSTICS changed = ROW_COUNT;
    IF changed <> 0 THEN
        RAISE EXCEPTION 'schema assert: replayed step accepted (% rows)', changed;
    END IF;

    -- 更早的步（乱序 / 旧窗口码）同样必须 0 行
    UPDATE mfa_factors SET last_used_step = 99, last_used_at = now()
    WHERE factor_id = '__assert_replay_factor__'
      AND (last_used_step IS NULL OR last_used_step < 99);
    GET DIAGNOSTICS changed = ROW_COUNT;
    IF changed <> 0 THEN
        RAISE EXCEPTION 'schema assert: stale step accepted (% rows)', changed;
    END IF;

    -- 更晚的步（正常下一次验证）可推进
    UPDATE mfa_factors SET last_used_step = 101, last_used_at = now()
    WHERE factor_id = '__assert_replay_factor__'
      AND (last_used_step IS NULL OR last_used_step < 101);
    GET DIAGNOSTICS changed = ROW_COUNT;
    IF changed <> 1 THEN
        RAISE EXCEPTION 'schema assert: next step rejected (% rows)', changed;
    END IF;

    SELECT last_used_step INTO current_step
    FROM mfa_factors WHERE factor_id = '__assert_replay_factor__';
    IF current_step IS DISTINCT FROM 101 THEN
        RAISE EXCEPTION 'schema assert: last_used_step not advanced (got %)', current_step;
    END IF;
END $$;

-- 3) 新因子默认 NULL（不阻塞首次验证）
DO $$
DECLARE
    fresh bigint;
BEGIN
    INSERT INTO mfa_factors (factor_id, user_id, secret_encrypted)
    VALUES ('__assert_replay_fresh__', '__assert_replay_user__', '\x00'::bytea);
    SELECT last_used_step INTO fresh
    FROM mfa_factors WHERE factor_id = '__assert_replay_fresh__';
    IF fresh IS NOT NULL THEN
        RAISE EXCEPTION 'schema assert: last_used_step default is not NULL (%)', fresh;
    END IF;
END $$;

ROLLBACK;
