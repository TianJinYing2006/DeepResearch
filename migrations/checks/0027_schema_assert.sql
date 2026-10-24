-- 0027 结构自检：Passkey（需求 20 P1-2）
--
-- 用法（CI infra job 与本地验证共用）：
--   docker compose -f docker-compose.staging.yml exec -T postgres \
--     psql -U deepresearch -d deepresearch -v ON_ERROR_STOP=1 -f - < migrations/checks/0027_schema_assert.sql
--
-- 全部写入都在同一事务内并最终 ROLLBACK：只验行为，不落数据。

BEGIN;

-- 0) 造一个用户以承载外键（assert 事务内，最终回滚）
INSERT INTO users (user_id, email, password_hash)
VALUES ('__assert_passkey_user__', 'passkey-assert@example.com', 'x');

-- 1) credential_id 全局唯一（防跨账号重放）
INSERT INTO webauthn_credentials (credential_id, user_id, public_key)
VALUES ('\x01'::bytea, '__assert_passkey_user__', '\x02'::bytea);
DO $$
BEGIN
    BEGIN
        INSERT INTO webauthn_credentials (credential_id, user_id, public_key)
        VALUES ('\x01'::bytea, '__assert_passkey_user__', '\x03'::bytea);
        RAISE EXCEPTION 'schema assert: duplicate credential_id accepted';
    EXCEPTION WHEN unique_violation THEN
        NULL;
    END;
END $$;

-- 2) sign_count 不得为负
DO $$
BEGIN
    BEGIN
        INSERT INTO webauthn_credentials (credential_id, user_id, public_key, sign_count)
        VALUES ('\x04'::bytea, '__assert_passkey_user__', '\x02'::bytea, -1);
        RAISE EXCEPTION 'schema assert: negative sign_count accepted';
    EXCEPTION WHEN check_violation THEN
        NULL;
    END;
END $$;

-- 3) 挑战 purpose 白名单
DO $$
BEGIN
    BEGIN
        INSERT INTO webauthn_challenges (challenge_id, user_id, purpose, challenge, expires_at)
        VALUES ('__assert_bad_purpose__', '__assert_passkey_user__', 'other',
                '\x05'::bytea, now() + interval '5 minutes');
        RAISE EXCEPTION 'schema assert: webauthn_challenges.purpose accepted invalid value';
    EXCEPTION WHEN check_violation THEN
        NULL;
    END;
END $$;

-- 4) 挑战可落库（register / authenticate）
INSERT INTO webauthn_challenges (challenge_id, user_id, purpose, challenge, expires_at)
VALUES ('__assert_register__', '__assert_passkey_user__', 'register',
        '\x06'::bytea, now() + interval '5 minutes');
INSERT INTO webauthn_challenges (challenge_id, user_id, purpose, challenge, expires_at)
VALUES ('__assert_authenticate__', NULL, 'authenticate',
        '\x07'::bytea, now() + interval '5 minutes');

-- 5) 级联删除：删用户 → 凭据与绑定挑战随之删除
DELETE FROM users WHERE user_id = '__assert_passkey_user__';
DO $$
DECLARE
    credentials integer;
    challenges integer;
BEGIN
    SELECT count(*) INTO credentials
    FROM webauthn_credentials WHERE user_id = '__assert_passkey_user__';
    SELECT count(*) INTO challenges
    FROM webauthn_challenges WHERE user_id = '__assert_passkey_user__';
    IF credentials <> 0 OR challenges <> 0 THEN
        RAISE EXCEPTION 'schema assert: cascade delete failed (credentials=%, challenges=%)',
            credentials, challenges;
    END IF;
END $$;

-- 6) 关键列与索引必须存在
DO $$
DECLARE
    missing text;
BEGIN
    SELECT string_agg(c, ', ') INTO missing
    FROM (VALUES
        ('webauthn_credentials.credential_id'), ('webauthn_credentials.user_id'),
        ('webauthn_credentials.public_key'), ('webauthn_credentials.sign_count'),
        ('webauthn_credentials.transports'), ('webauthn_credentials.aaguid'),
        ('webauthn_credentials.backup_state'), ('webauthn_credentials.device_type'),
        ('webauthn_credentials.name'), ('webauthn_credentials.created_at'),
        ('webauthn_credentials.last_used_at'),
        ('webauthn_challenges.challenge_id'), ('webauthn_challenges.user_id'),
        ('webauthn_challenges.purpose'), ('webauthn_challenges.challenge'),
        ('webauthn_challenges.created_at'), ('webauthn_challenges.expires_at'),
        ('webauthn_challenges.consumed_at')
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
        ('webauthn_credentials_user_idx'),
        ('webauthn_challenges_expiry_idx'),
        ('webauthn_challenges_user_idx')
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
