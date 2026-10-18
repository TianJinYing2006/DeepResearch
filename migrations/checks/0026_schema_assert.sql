-- 0026 结构自检：MFA（需求 20 P1-3）。
--
-- 用法（CI infra job 与本地验证共用）：
--   docker compose -f docker-compose.staging.yml exec -T postgres \
--     psql -U deepresearch -d deepresearch -v ON_ERROR_STOP=1 -f - < migrations/checks/0026_schema_assert.sql
--
-- 全部写入都在同一事务内并最终 ROLLBACK：只验行为，不落数据。

BEGIN;

-- 0) 造一个用户以承载外键（assert 事务内，最终回滚）
INSERT INTO users (user_id, email, password_hash)
VALUES ('__assert_mfa_user__', 'mfa-assert@example.com', 'x');

-- 1) 未确认因子可重复暂存（仅一条「已确认」唯一）
INSERT INTO mfa_factors (factor_id, user_id, secret_encrypted)
VALUES ('__assert_factor_1__', '__assert_mfa_user__', '\x00'::bytea);
INSERT INTO mfa_factors (factor_id, user_id, secret_encrypted)
VALUES ('__assert_factor_2__', '__assert_mfa_user__', '\x00'::bytea);

-- 2) 第二条「已确认」必须被拒（user_id, type 唯一）
UPDATE mfa_factors SET confirmed_at = now() WHERE factor_id = '__assert_factor_1__';
DO $$
BEGIN
    BEGIN
        UPDATE mfa_factors SET confirmed_at = now() WHERE factor_id = '__assert_factor_2__';
        RAISE EXCEPTION 'schema assert: duplicate confirmed factor accepted';
    EXCEPTION WHEN unique_violation THEN
        NULL;
    END;
END $$;

-- 3) 类型白名单：非法值必须被拒绝
DO $$
BEGIN
    BEGIN
        INSERT INTO mfa_factors (factor_id, user_id, secret_encrypted, type)
        VALUES ('__assert_factor_bad__', '__assert_mfa_user__', '\x00'::bytea, 'sms');
        RAISE EXCEPTION 'schema assert: mfa_factors.type accepted invalid value';
    EXCEPTION WHEN check_violation THEN
        NULL;
    END;
END $$;

-- 4) 恢复码：同一用户同一摘要唯一
INSERT INTO recovery_codes (user_id, code_hash) VALUES ('__assert_mfa_user__', '__assert_rc__');
DO $$
BEGIN
    BEGIN
        INSERT INTO recovery_codes (user_id, code_hash)
        VALUES ('__assert_mfa_user__', '__assert_rc__');
        RAISE EXCEPTION 'schema assert: duplicate recovery code accepted';
    EXCEPTION WHEN unique_violation THEN
        NULL;
    END;
END $$;

-- 5) 级联删除：删用户 ⇒ 因子与恢复码随之删除
DELETE FROM users WHERE user_id = '__assert_mfa_user__';
DO $$
DECLARE
    factors integer;
    codes integer;
BEGIN
    SELECT count(*) INTO factors FROM mfa_factors WHERE user_id = '__assert_mfa_user__';
    SELECT count(*) INTO codes FROM recovery_codes WHERE user_id = '__assert_mfa_user__';
    IF factors <> 0 OR codes <> 0 THEN
        RAISE EXCEPTION 'schema assert: cascade delete failed (factors=%, codes=%)', factors, codes;
    END IF;
END $$;

-- 6) 关键列与索引必须存在
DO $$
DECLARE
    missing text;
BEGIN
    SELECT string_agg(c, ', ') INTO missing
    FROM (VALUES
        ('mfa_factors.factor_id'), ('mfa_factors.user_id'), ('mfa_factors.type'),
        ('mfa_factors.secret_encrypted'), ('mfa_factors.name'),
        ('mfa_factors.created_at'), ('mfa_factors.confirmed_at'),
        ('mfa_factors.last_used_at'),
        ('recovery_codes.id'), ('recovery_codes.user_id'),
        ('recovery_codes.code_hash'), ('recovery_codes.created_at'),
        ('recovery_codes.used_at'),
        ('sessions.mfa_verified')
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

    SELECT string_agg(i, ', ') INTO missing
    FROM (VALUES
        ('mfa_factors_user_idx'), ('mfa_factors_confirmed_uk'),
        ('recovery_codes_hash_uk'), ('recovery_codes_user_idx')
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
        WHERE conname = 'mfa_factors_user_id_fkey' AND contype = 'f'
    ) THEN
        RAISE EXCEPTION 'schema assert: mfa_factors_user_id_fkey missing';
    END IF;
END $$;

-- 7) 会话默认 mfa_verified = true（非 MFA 用户零行为变化）
DO $$
DECLARE
    dflt text;
BEGIN
    SELECT column_default INTO dflt
    FROM information_schema.columns
    WHERE table_name = 'sessions' AND column_name = 'mfa_verified';
    IF dflt IS NULL OR dflt NOT LIKE '%true%' THEN
        RAISE EXCEPTION 'schema assert: sessions.mfa_verified default is %', dflt;
    END IF;
END $$;

ROLLBACK;
