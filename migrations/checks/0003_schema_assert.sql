-- 0003 结构自检：users / sessions / invites 的约束与级联行为。
--
-- 用法与其它 checks 相同（psql -v ON_ERROR_STOP=1），全部写入后 ROLLBACK。

BEGIN;

-- 1) 关键列存在
DO $$
DECLARE
    missing text;
BEGIN
    SELECT string_agg(c, ', ') INTO missing
    FROM (VALUES
        ('users.user_id'), ('users.email'), ('users.password_hash'), ('users.status'),
        ('users.last_login_at'),
        ('sessions.token_hash'), ('sessions.user_id'), ('sessions.expires_at'),
        ('invites.code_hash'), ('invites.used_by'), ('invites.used_at'), ('invites.revoked_at')
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

-- 2) 邮箱唯一性按 lower(email)：大小写不同的同一邮箱必须被拒
INSERT INTO users (user_id, email, password_hash) VALUES ('__assert_u1__', 'A@example.com', 'x');
DO $$
BEGIN
    BEGIN
        INSERT INTO users (user_id, email, password_hash) VALUES ('__assert_u2__', 'a@EXAMPLE.com', 'x');
        RAISE EXCEPTION 'schema assert: case-insensitive email uniqueness missing';
    EXCEPTION WHEN unique_violation THEN
        NULL;
    END;
END $$;

-- 3) 非法状态必须被 CHECK 拒绝
DO $$
BEGIN
    BEGIN
        INSERT INTO users (user_id, email, password_hash, status)
        VALUES ('__assert_u3__', 'b@example.com', 'x', 'deleted');
        RAISE EXCEPTION 'schema assert: users.status accepted invalid value';
    EXCEPTION WHEN check_violation THEN
        NULL;
    END;
END $$;

-- 4) 会话外键 + 级联：删用户 → 会话消失
INSERT INTO sessions (token_hash, user_id, expires_at)
VALUES ('__assert_token__', '__assert_u1__', now() + interval '1 day');
DELETE FROM users WHERE user_id = '__assert_u1__';
DO $$
DECLARE
    n integer;
BEGIN
    SELECT count(*) INTO n FROM sessions WHERE token_hash = '__assert_token__';
    IF n <> 0 THEN
        RAISE EXCEPTION 'schema assert: ON DELETE CASCADE for sessions failed';
    END IF;
END $$;

-- 5) 邀请码外键：未知用户不能被标记 used_by
DO $$
BEGIN
    BEGIN
        INSERT INTO invites (code_hash, used_by) VALUES ('__assert_inv__', '__no_such_user__');
        RAISE EXCEPTION 'schema assert: orphan invites.used_by accepted';
    EXCEPTION WHEN foreign_key_violation THEN
        NULL;
    END;
END $$;

-- 6) runs.user_id 外键存在：未知用户不能被写进 runs
DO $$
BEGIN
    BEGIN
        INSERT INTO runs (run_id, topic, user_id) VALUES ('__assert_run__', 't', '__no_such_user__');
        RAISE EXCEPTION 'schema assert: unknown runs.user_id accepted';
    EXCEPTION WHEN foreign_key_violation THEN
        NULL;
    END;
END $$;

ROLLBACK;
