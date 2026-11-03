-- 0020 结构自检：产品反馈台账 + 注册同意留档（需求 25 批次 A4）。
--
-- 用法（CI infra job 与本地验证共用）：
--   docker compose -f docker-compose.staging.yml exec -T postgres \
--     psql -U deepresearch -d deepresearch -v ON_ERROR_STOP=1 -f - < migrations/checks/0020_schema_assert.sql
--
-- 全部写入都在同一事务内并最终 ROLLBACK：只验行为，不落数据。

BEGIN;

-- 1) 表与关键列必须存在
DO $$
DECLARE
    missing text;
BEGIN
    SELECT string_agg(c, ', ') INTO missing
    FROM (VALUES
        ('user_feedback.feedback_id'), ('user_feedback.user_id'),
        ('user_feedback.category'), ('user_feedback.message'),
        ('user_feedback.contact'), ('user_feedback.page'),
        ('user_feedback.request_id'), ('user_feedback.status'),
        ('user_feedback.created_at'),
        ('user_consents.user_id'), ('user_consents.doc_type'),
        ('user_consents.version'), ('user_consents.agreed_at'),
        ('user_consents.ip_hash')
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

-- 1b) 契约要求可空的列必须仍可空（ip_hash 只存摘要；contact/page/request_id 可选）
DO $$
DECLARE
    not_nullable text;
BEGIN
    SELECT string_agg(c, ', ') INTO not_nullable
    FROM (VALUES
        ('user_feedback.contact'), ('user_feedback.page'),
        ('user_feedback.request_id'), ('user_consents.ip_hash')
    ) AS v(c)
    WHERE NOT EXISTS (
        SELECT 1 FROM information_schema.columns col
        WHERE col.table_schema = 'public'
          AND col.table_name = split_part(v.c, '.', 1)
          AND col.column_name = split_part(v.c, '.', 2)
          AND col.is_nullable = 'YES'
    );
    IF not_nullable IS NOT NULL THEN
        RAISE EXCEPTION 'schema assert: columns should be nullable -> %', not_nullable;
    END IF;
END $$;

-- 2) user_feedback：分类白名单三值可写，非法值被拒；PK 必须生效
INSERT INTO user_feedback (feedback_id, user_id, category, message)
VALUES ('__assert_0020_bug__', '__assert_0020_user__', 'bug', 'm'),
       ('__assert_0020_idea__', '__assert_0020_user__', 'idea', 'm'),
       ('__assert_0020_other__', '__assert_0020_user__', 'other', 'm');

DO $$
BEGIN
    BEGIN
        INSERT INTO user_feedback (feedback_id, user_id, category, message)
        VALUES ('__assert_0020_bad_cat__', '__assert_0020_user__', 'marketing', 'm');
        RAISE EXCEPTION 'schema assert: user_feedback.category accepted invalid value';
    EXCEPTION WHEN check_violation THEN
        NULL;
    END;
    BEGIN
        INSERT INTO user_feedback (feedback_id, user_id, category, message)
        VALUES ('__assert_0020_bug__', '__assert_0020_user__', 'bug', 'm');
        RAISE EXCEPTION 'schema assert: user_feedback accepted duplicate feedback_id';
    EXCEPTION WHEN unique_violation THEN
        NULL;
    END;
    BEGIN
        INSERT INTO user_feedback (feedback_id, user_id, category, message)
        VALUES ('__assert_0020_null_msg__', '__assert_0020_user__', 'bug', NULL);
        RAISE EXCEPTION 'schema assert: user_feedback.message accepted NULL';
    EXCEPTION WHEN not_null_violation THEN
        NULL;
    END;
END $$;

-- 3) user_feedback.status：默认 new、三值白名单、非法值被拒
DO $$
DECLARE
    n integer;
BEGIN
    SELECT count(*) INTO n FROM user_feedback
     WHERE feedback_id LIKE '__assert_0020_%__' AND status = 'new';
    IF n <> 3 THEN
        RAISE EXCEPTION 'schema assert: user_feedback default status is not new (rows=%)', n;
    END IF;
END $$;

UPDATE user_feedback SET status = 'reviewing' WHERE feedback_id = '__assert_0020_bug__';
UPDATE user_feedback SET status = 'closed'    WHERE feedback_id = '__assert_0020_idea__';

DO $$
BEGIN
    BEGIN
        UPDATE user_feedback SET status = 'done' WHERE feedback_id = '__assert_0020_other__';
        RAISE EXCEPTION 'schema assert: user_feedback.status accepted invalid value';
    EXCEPTION WHEN check_violation THEN
        NULL;
    END;
END $$;

-- 4) user_consents：PK (user_id, doc_type)、doc_type 白名单、ip_hash 可空、version 必填
INSERT INTO user_consents (user_id, doc_type, version)
VALUES ('__assert_0020_user__', 'terms', 'v1');
INSERT INTO user_consents (user_id, doc_type, version, ip_hash)
VALUES ('__assert_0020_user__', 'privacy', 'v1', 'sha256-assert');

DO $$
DECLARE
    n integer;
BEGIN
    SELECT count(*) INTO n FROM user_consents
     WHERE user_id = '__assert_0020_user__' AND doc_type IN ('terms', 'privacy');
    IF n <> 2 THEN
        RAISE EXCEPTION 'schema assert: user_consents should allow terms+privacy per user (rows=%)', n;
    END IF;
    SELECT count(*) INTO n FROM user_consents
     WHERE user_id = '__assert_0020_user__' AND doc_type = 'terms' AND ip_hash IS NULL;
    IF n <> 1 THEN
        RAISE EXCEPTION 'schema assert: user_consents.ip_hash should be nullable';
    END IF;
    BEGIN
        INSERT INTO user_consents (user_id, doc_type, version)
        VALUES ('__assert_0020_user__', 'terms', 'v2');
        RAISE EXCEPTION 'schema assert: user_consents accepted duplicate (user_id, doc_type)';
    EXCEPTION WHEN unique_violation THEN
        NULL;
    END;
    BEGIN
        INSERT INTO user_consents (user_id, doc_type, version)
        VALUES ('__assert_0020_user__', 'marketing', 'v1');
        RAISE EXCEPTION 'schema assert: user_consents.doc_type accepted invalid value';
    EXCEPTION WHEN check_violation THEN
        NULL;
    END;
    BEGIN
        INSERT INTO user_consents (user_id, doc_type, version)
        VALUES ('__assert_0020_user2__', 'terms', NULL);
        RAISE EXCEPTION 'schema assert: user_consents.version accepted NULL';
    EXCEPTION WHEN not_null_violation THEN
        NULL;
    END;
END $$;

ROLLBACK;
