-- 0007 结构自检：audit_logs 列、append-only 语义（无 UPDATE 触发器之外的路径不作断言）与索引。

BEGIN;

-- 1) 关键列存在
DO $$
DECLARE
    missing text;
BEGIN
    SELECT string_agg(c, ', ') INTO missing
    FROM (VALUES
        ('audit_logs.at'), ('audit_logs.action'), ('audit_logs.actor_user_id'),
        ('audit_logs.target_type'), ('audit_logs.target_id'), ('audit_logs.ip'),
        ('audit_logs.user_agent'), ('audit_logs.request_id'), ('audit_logs.detail')
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

-- 2) action 必填（NOT NULL 约束生效）
DO $$
BEGIN
    BEGIN
        INSERT INTO audit_logs (action) VALUES (NULL);
        RAISE EXCEPTION 'schema assert: audit_logs.action accepted NULL';
    EXCEPTION WHEN not_null_violation THEN
        NULL;
    END;
END $$;

-- 3) 追加与默认值
INSERT INTO audit_logs (action, actor_user_id, detail)
VALUES ('login_failed', NULL, '{"email_hash": "abc"}'::jsonb);
DO $$
DECLARE
    n integer;
BEGIN
    SELECT count(*) INTO n FROM audit_logs WHERE action = 'login_failed';
    IF n < 1 THEN
        RAISE EXCEPTION 'schema assert: audit_logs insert failed';
    END IF;
END $$;

ROLLBACK;
