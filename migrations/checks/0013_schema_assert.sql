-- 0013 结构自检：alert_states / alert_deliveries 表、约束、部分索引与交付语义。

BEGIN;

DO $$
DECLARE
    missing text;
BEGIN
    SELECT string_agg(c, ', ') INTO missing
    FROM (VALUES
        ('alert_states.fingerprint'), ('alert_states.severity'), ('alert_states.status'),
        ('alert_states.detail'), ('alert_states.last_notified_at'),
        ('alert_deliveries.delivery_id'), ('alert_deliveries.fingerprint'),
        ('alert_deliveries.kind'), ('alert_deliveries.severity'), ('alert_deliveries.payload'),
        ('alert_deliveries.attempts'), ('alert_deliveries.max_attempts'),
        ('alert_deliveries.next_attempt_at'), ('alert_deliveries.delivered_at'),
        ('alert_deliveries.last_error')
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
BEGIN
    BEGIN
        INSERT INTO alert_deliveries (fingerprint, kind, severity, payload)
        VALUES ('assert', 'bad-kind', 'high', '{}');
        RAISE EXCEPTION 'schema assert: alert_deliveries.kind accepted invalid value';
    EXCEPTION WHEN check_violation THEN
        NULL;
    END;
END $$;

DO $$
DECLARE
    n integer;
BEGIN
    SELECT count(*) INTO n FROM pg_indexes
     WHERE schemaname = 'public' AND indexname = 'alert_deliveries_due_idx'
       AND indexdef ILIKE '%WHERE (delivered_at IS NULL)%';
    IF n < 1 THEN
        RAISE EXCEPTION 'schema assert: alert_deliveries_due_idx partial index missing';
    END IF;
END $$;

INSERT INTO alert_states (fingerprint, severity, status, detail, last_notified_at)
VALUES ('__assert_alert__', 'high', 'firing', '{"code": "__assert_alert__"}', now())
ON CONFLICT (fingerprint) DO UPDATE
    SET status = EXCLUDED.status, detail = EXCLUDED.detail, updated_at = now();

INSERT INTO alert_deliveries (fingerprint, kind, severity, payload, attempts, next_attempt_at)
VALUES ('__assert_alert__', 'firing', 'high', '{"message": "assert"}', 1, now());

DO $$
DECLARE
    n integer;
BEGIN
    SELECT count(*) INTO n FROM alert_deliveries
     WHERE fingerprint = '__assert_alert__' AND delivered_at IS NULL;
    IF n < 1 THEN
        RAISE EXCEPTION 'schema assert: pending alert delivery not persisted';
    END IF;
END $$;

ROLLBACK;
