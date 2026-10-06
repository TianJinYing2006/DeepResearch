-- 0016 结构自检：quota_reservations 列、约束与「hold 随 run 级联」语义。

BEGIN;

DO $$
DECLARE
    missing text;
BEGIN
    SELECT string_agg(c, ', ') INTO missing
    FROM (VALUES
        ('quota_reservations.reservation_id'), ('quota_reservations.run_id'),
        ('quota_reservations.user_id'), ('quota_reservations.period'),
        ('quota_reservations.reserved_cny'), ('quota_reservations.actual_cny'),
        ('quota_reservations.released_cny'), ('quota_reservations.status'),
        ('quota_reservations.created_at'), ('quota_reservations.updated_at')
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

INSERT INTO runs (run_id, topic) VALUES ('__assert_quota_run__', 't')
ON CONFLICT DO NOTHING;

-- 1) 非法状态 / 负数 hold 必须被拒绝
DO $$
BEGIN
    BEGIN
        INSERT INTO quota_reservations (reservation_id, run_id, period, reserved_cny, status)
        VALUES ('__assert_qr_bad__', '__assert_quota_run__', current_date, 1.0, 'whatever');
        RAISE EXCEPTION 'schema assert: quota_reservations.status accepted invalid value';
    EXCEPTION WHEN check_violation THEN
        NULL;
    END;
    BEGIN
        INSERT INTO quota_reservations (reservation_id, run_id, period, reserved_cny)
        VALUES ('__assert_qr_neg__', '__assert_quota_run__', current_date, -0.01);
        RAISE EXCEPTION 'schema assert: quota_reservations.reserved_cny accepted negative';
    EXCEPTION WHEN check_violation THEN
        NULL;
    END;
END $$;

-- 2) 一个 run 只能有一条预留（并发准入的数据库级兜底）
INSERT INTO quota_reservations (reservation_id, run_id, period, reserved_cny)
VALUES ('__assert_qr__', '__assert_quota_run__', current_date, 1.5);
DO $$
BEGIN
    BEGIN
        INSERT INTO quota_reservations (reservation_id, run_id, period, reserved_cny)
        VALUES ('__assert_qr_dup__', '__assert_quota_run__', current_date, 1.5);
        RAISE EXCEPTION 'schema assert: duplicate reservation for same run accepted';
    EXCEPTION WHEN unique_violation THEN
        NULL;
    END;
END $$;

-- 3) 结算语义：reserved → settled 且 actual/released 落库
UPDATE quota_reservations
   SET status = 'settled', actual_cny = 0.5,
       released_cny = GREATEST(reserved_cny - 0.5, 0), updated_at = now()
 WHERE reservation_id = '__assert_qr__';
DO $$
DECLARE
    n integer;
BEGIN
    SELECT count(*) INTO n FROM quota_reservations
     WHERE reservation_id = '__assert_qr__'
       AND status = 'settled' AND actual_cny = 0.5 AND released_cny = 1.0;
    IF n <> 1 THEN
        RAISE EXCEPTION 'schema assert: quota_reservations settle semantics broken';
    END IF;
END $$;

-- 4) run 删除时预留级联清理（保留期清理不留孤儿 hold）
DELETE FROM runs WHERE run_id = '__assert_quota_run__';
DO $$
DECLARE
    n integer;
BEGIN
    SELECT count(*) INTO n FROM quota_reservations
     WHERE run_id = '__assert_quota_run__';
    IF n <> 0 THEN
        RAISE EXCEPTION 'schema assert: quota_reservations did not cascade on run delete';
    END IF;
END $$;

ROLLBACK;
