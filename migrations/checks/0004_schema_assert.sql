-- 0004 结构自检：moderation_records 的约束与脱钩行为、runs.moderation_status 取值约束。

BEGIN;

-- 1) 关键列存在
DO $$
DECLARE
    missing text;
BEGIN
    SELECT string_agg(c, ', ') INTO missing
    FROM (VALUES
        ('moderation_records.user_id'), ('moderation_records.run_id'),
        ('moderation_records.kind'), ('moderation_records.detail'),
        ('moderation_records.created_at'), ('runs.moderation_status')
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

-- 2) kind 必须被 CHECK 拒绝非法值
DO $$
BEGIN
    BEGIN
        INSERT INTO moderation_records (kind) VALUES ('not_a_kind');
        RAISE EXCEPTION 'schema assert: moderation_records.kind accepted invalid value';
    EXCEPTION WHEN check_violation THEN
        NULL;
    END;
END $$;

-- 3) 用户/任务删除后记录脱钩（SET NULL），记录本身保留
INSERT INTO users (user_id, email, password_hash)
VALUES ('__assert_mod_user__', '__assert_mod@example.com', 'x')
ON CONFLICT DO NOTHING;
INSERT INTO runs (run_id, topic, user_id, moderation_status)
VALUES ('__assert_mod_run__', 't', '__assert_mod_user__', 'flagged')
ON CONFLICT (run_id) DO UPDATE SET moderation_status = 'flagged';
INSERT INTO moderation_records (user_id, run_id, kind, detail)
VALUES ('__assert_mod_user__', '__assert_mod_run__', 'output_flagged', '{"matches": ["x"]}'::jsonb);

DELETE FROM runs WHERE run_id = '__assert_mod_run__';
DO $$
DECLARE
    n integer;
BEGIN
    SELECT count(*) INTO n FROM moderation_records
     WHERE run_id IS NULL AND kind = 'output_flagged'
       AND detail->>'matches' = '["x"]';
    IF n < 1 THEN
        RAISE EXCEPTION 'schema assert: moderation_records did not survive run deletion (SET NULL)';
    END IF;
END $$;

-- 4) runs.moderation_status 只接受 flagged / blocked（用新行验证：上一行已删）
INSERT INTO runs (run_id, topic, user_id)
VALUES ('__assert_mod_run2__', 't', '__assert_mod_user__');
DO $$
BEGIN
    BEGIN
        UPDATE runs SET moderation_status = 'whatever' WHERE run_id = '__assert_mod_run2__';
        RAISE EXCEPTION 'schema assert: runs.moderation_status accepted invalid value';
    EXCEPTION WHEN check_violation THEN
        NULL;
    END;
END $$;

ROLLBACK;
