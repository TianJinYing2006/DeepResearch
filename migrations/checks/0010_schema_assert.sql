-- 0010 结构自检：sessions 新列与 password_reset_tokens 约束。

BEGIN;

DO $$
DECLARE
    missing text;
BEGIN
    SELECT string_agg(c, ', ') INTO missing
    FROM (VALUES
        ('sessions.session_id'), ('sessions.last_seen_at'),
        ('sessions.ip'), ('sessions.user_agent'),
        ('password_reset_tokens.token_hash'), ('password_reset_tokens.user_id'),
        ('password_reset_tokens.expires_at'), ('password_reset_tokens.consumed_at')
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

-- session_id 自动生成且唯一
INSERT INTO users (user_id, email, password_hash)
VALUES ('__assert_sess_user__', '__assert_sess@example.com', 'x')
ON CONFLICT DO NOTHING;
INSERT INTO sessions (token_hash, user_id, expires_at)
VALUES ('__assert_sess_tok__', '__assert_sess_user__', now() + interval '1 hour');
DO $$
DECLARE
    sid text;
BEGIN
    SELECT session_id INTO sid FROM sessions WHERE token_hash = '__assert_sess_tok__';
    IF sid IS NULL OR length(sid) < 8 THEN
        RAISE EXCEPTION 'schema assert: sessions.session_id not auto-generated';
    END IF;
END $$;

-- 重置 token 必须绑定用户（FK）
DO $$
BEGIN
    BEGIN
        INSERT INTO password_reset_tokens (token_hash, user_id, expires_at)
        VALUES ('__assert_reset_bad__', '__no_such_user__', now() + interval '30 min');
        RAISE EXCEPTION 'schema assert: password_reset_tokens accepted missing user';
    EXCEPTION WHEN foreign_key_violation THEN
        NULL;
    END;
END $$;

ROLLBACK;
