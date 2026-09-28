-- 0015 结构自检：审核生命周期取值域（cleared / under_review）与审核证据 kind 扩展。
-- 重点回归 0014 申诉 accepted 路径：真实 PG 必须接受 `runs.moderation_status='cleared'`。

BEGIN;

-- 1) 明确存在命名约束（而不是被静默丢弃）
DO $$
DECLARE
    n integer;
BEGIN
    SELECT count(*) INTO n FROM pg_constraint
     WHERE conrelid = 'runs'::regclass AND conname = 'runs_moderation_status_check';
    IF n < 1 THEN
        RAISE EXCEPTION 'schema assert: runs_moderation_status_check missing';
    END IF;
    SELECT count(*) INTO n FROM pg_constraint
     WHERE conrelid = 'moderation_records'::regclass
       AND conname = 'moderation_records_kind_check';
    IF n < 1 THEN
        RAISE EXCEPTION 'schema assert: moderation_records_kind_check missing';
    END IF;
END $$;

-- 2) 合法值：cleared / under_review / flagged / blocked 均可写
DO $$
DECLARE
    value text;
    n integer;
BEGIN
    FOREACH value IN ARRAY ARRAY['flagged', 'blocked', 'cleared', 'under_review']
    LOOP
        INSERT INTO runs (run_id, topic, moderation_status)
        VALUES ('__assert_mod_' || value || '__', 't', value)
        ON CONFLICT (run_id) DO UPDATE SET moderation_status = EXCLUDED.moderation_status;
        SELECT count(*) INTO n FROM runs
         WHERE run_id = '__assert_mod_' || value || '__'
           AND moderation_status = value;
        IF n <> 1 THEN
            RAISE EXCEPTION 'schema assert: moderation_status=% 未被接受', value;
        END IF;
    END LOOP;
END $$;

-- 3) 非法值仍必须被拒绝
DO $$
BEGIN
    BEGIN
        UPDATE runs SET moderation_status = 'whatever' WHERE run_id = '__assert_mod_flagged__';
        RAISE EXCEPTION 'schema assert: runs.moderation_status accepted invalid value';
    EXCEPTION WHEN check_violation THEN
        NULL;
    END;
END $$;

-- 4) moderation_records.kind：申诉决策与不可变决定可写，非法值仍拒绝
DO $$
BEGIN
    INSERT INTO moderation_records (kind, detail)
    VALUES ('appeal_accepted', '{"assert": true}'::jsonb),
           ('appeal_rejected', '{"assert": true}'::jsonb),
           ('output_decision', '{"assert": true}'::jsonb);
    BEGIN
        INSERT INTO moderation_records (kind) VALUES ('not_a_kind');
        RAISE EXCEPTION 'schema assert: moderation_records.kind accepted invalid value';
    EXCEPTION WHEN check_violation THEN
        NULL;
    END;
END $$;

ROLLBACK;
