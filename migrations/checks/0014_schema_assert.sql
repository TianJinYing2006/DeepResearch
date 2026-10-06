-- 0014 结构自检：moderation_appeals 表、约束、索引与唯一性语义。

BEGIN;

DO $$
DECLARE
    missing text;
BEGIN
    SELECT string_agg(c, ', ') INTO missing
    FROM (VALUES
        ('moderation_appeals.appeal_id'), ('moderation_appeals.run_id'),
        ('moderation_appeals.user_id'), ('moderation_appeals.message'),
        ('moderation_appeals.status'), ('moderation_appeals.decision_note'),
        ('moderation_appeals.reviewed_by'), ('moderation_appeals.sla_due_at'),
        ('moderation_appeals.decided_at'), ('moderation_appeals.created_at'),
        ('moderation_appeals.updated_at')
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

DO $$
DECLARE
    n integer;
BEGIN
    SELECT count(*) INTO n FROM pg_indexes
     WHERE schemaname = 'public' AND indexname = 'moderation_appeals_run_user_idx';
    IF n < 1 THEN
        RAISE EXCEPTION 'schema assert: moderation_appeals_run_user_idx missing';
    END IF;
END $$;

DO $$
BEGIN
    BEGIN
        INSERT INTO moderation_appeals (appeal_id, message, status)
        VALUES ('__assert_appeal_bad__', 't', 'whatever');
        RAISE EXCEPTION 'schema assert: moderation_appeals.status accepted invalid value';
    EXCEPTION WHEN check_violation THEN
        NULL;
    END;
END $$;

INSERT INTO runs (run_id, topic) VALUES ('__assert_appeal_run__', 't')
ON CONFLICT DO NOTHING;

INSERT INTO moderation_appeals (appeal_id, run_id, user_id, message, sla_due_at)
VALUES ('__assert_appeal__', '__assert_appeal_run__', 'u1', '申诉', now() + interval '72 hours');

DO $$
DECLARE
    n integer;
BEGIN
    BEGIN
        INSERT INTO moderation_appeals (appeal_id, run_id, user_id, message)
        VALUES ('__assert_appeal_dup__', '__assert_appeal_run__', 'u1', '二次申诉');
        RAISE EXCEPTION 'schema assert: duplicate appeal for same run+user accepted';
    EXCEPTION WHEN unique_violation THEN
        NULL;
    END;

    SELECT count(*) INTO n FROM moderation_appeals
     WHERE appeal_id = '__assert_appeal__' AND status = 'pending' AND sla_due_at IS NOT NULL;
    IF n < 1 THEN
        RAISE EXCEPTION 'schema assert: pending appeal with SLA not persisted';
    END IF;
END $$;

ROLLBACK;
