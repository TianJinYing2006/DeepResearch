-- 0005 结构自检：注销台账 / outbox 的约束、级联与唯一性。

BEGIN;

-- 1) 关键列存在
DO $$
DECLARE
    missing text;
BEGIN
    SELECT string_agg(c, ', ') INTO missing
    FROM (VALUES
        ('account_deletions.request_id'), ('account_deletions.user_id'),
        ('account_deletions.status'), ('account_deletions.attempts'),
        ('account_deletions.requested_at'), ('account_deletions.completed_at'),
        ('deletion_outbox.request_id'), ('deletion_outbox.target'),
        ('deletion_outbox.payload'), ('deletion_outbox.status'),
        ('deletion_outbox.attempts'), ('deletion_outbox.next_attempt_at'),
        ('deletion_outbox.lease_expires_at'), ('deletion_outbox.claimed_by'),
        ('deletion_outbox.last_error')
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

-- 2) 非法 status / target 必须被 CHECK 拒绝
DO $$
BEGIN
    BEGIN
        INSERT INTO account_deletions (request_id, user_id, status)
        VALUES ('__assert_del_bad__', 'u', 'whatever');
        RAISE EXCEPTION 'schema assert: account_deletions.status accepted invalid value';
    EXCEPTION WHEN check_violation THEN
        NULL;
    END;
END $$;

-- 3) (request_id, target) 唯一：重复登记同目标被拒
INSERT INTO account_deletions (request_id, user_id) VALUES ('__assert_del__', '__assert_del_user__');
INSERT INTO deletion_outbox (request_id, target) VALUES ('__assert_del__', 'qdrant');
DO $$
BEGIN
    BEGIN
        INSERT INTO deletion_outbox (request_id, target) VALUES ('__assert_del__', 'qdrant');
        RAISE EXCEPTION 'schema assert: deletion_outbox accepted duplicate (request_id, target)';
    EXCEPTION WHEN unique_violation THEN
        NULL;
    END;
END $$;

-- 4) 台账删除级联清 outbox
DELETE FROM account_deletions WHERE request_id = '__assert_del__';
DO $$
DECLARE
    n integer;
BEGIN
    SELECT count(*) INTO n FROM deletion_outbox WHERE request_id = '__assert_del__';
    IF n <> 0 THEN
        RAISE EXCEPTION 'schema assert: deletion_outbox rows survived ledger deletion (cascade)';
    END IF;
END $$;

ROLLBACK;
