-- 0022 结构自检：snapshots / run_snapshots 两表与内容寻址 / 级联语义
-- （需求 34 E1；与 0017 同模式：BEGIN…ROLLBACK，不留残迹）

BEGIN;

-- 1) 表与关键列存在
DO $$
DECLARE
    n integer;
BEGIN
    SELECT count(*) INTO n FROM information_schema.tables
     WHERE table_schema = 'public' AND table_name IN ('snapshots', 'run_snapshots');
    IF n <> 2 THEN
        RAISE EXCEPTION 'schema assert: snapshots/run_snapshots missing';
    END IF;

    SELECT count(*) INTO n FROM information_schema.columns
     WHERE table_schema = 'public' AND table_name = 'snapshots'
       AND column_name IN ('sha256', 'url', 'content_type', 'source_bytes', 'text',
                           'text_truncated', 'extractor_version', 'created_at');
    IF n <> 8 THEN
        RAISE EXCEPTION 'schema assert: snapshots columns mismatch (found %)', n;
    END IF;
END $$;

-- 2) 内容寻址主键：同 sha256 二次插入被忽略（ON CONFLICT DO NOTHING 语义）
INSERT INTO snapshots (sha256, url, text, extractor_version)
VALUES ('__assert_sha__', 'https://example.com/a', 'hello', 'webtext.v1');

INSERT INTO snapshots (sha256, url, text, extractor_version)
VALUES ('__assert_sha__', 'https://example.com/a', 'hello', 'webtext.v1')
ON CONFLICT (sha256) DO NOTHING;

DO $$
DECLARE
    n integer;
BEGIN
    SELECT count(*) INTO n FROM snapshots WHERE sha256 = '__assert_sha__';
    IF n <> 1 THEN
        RAISE EXCEPTION 'schema assert: content-addressed dedup broken (rows=%)', n;
    END IF;
END $$;

-- 3) run 关联去重 + 删除 run 级联解除（快照本体保留）
INSERT INTO runs (run_id, topic) VALUES ('__assert_rs_run__', 'assert')
ON CONFLICT (run_id) DO NOTHING;

INSERT INTO run_snapshots (run_id, sha256) VALUES ('__assert_rs_run__', '__assert_sha__');
INSERT INTO run_snapshots (run_id, sha256) VALUES ('__assert_rs_run__', '__assert_sha__')
ON CONFLICT DO NOTHING;

DO $$
DECLARE
    n integer;
BEGIN
    SELECT count(*) INTO n FROM run_snapshots WHERE run_id = '__assert_rs_run__';
    IF n <> 1 THEN
        RAISE EXCEPTION 'schema assert: run_snapshots dedup broken (rows=%)', n;
    END IF;
END $$;

DELETE FROM runs WHERE run_id = '__assert_rs_run__';

DO $$
DECLARE
    n integer;
BEGIN
    SELECT count(*) INTO n FROM run_snapshots WHERE run_id = '__assert_rs_run__';
    IF n <> 0 THEN
        RAISE EXCEPTION 'schema assert: run delete did not cascade run_snapshots (rows=%)', n;
    END IF;
    SELECT count(*) INTO n FROM snapshots WHERE sha256 = '__assert_sha__';
    IF n <> 1 THEN
        RAISE EXCEPTION 'schema assert: snapshot body should survive run delete (rows=%)', n;
    END IF;
END $$;

ROLLBACK;
