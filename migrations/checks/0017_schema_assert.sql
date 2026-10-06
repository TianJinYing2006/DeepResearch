-- 0017 结构自检：RAG 活跃去重唯一索引与并发插入语义。

BEGIN;

DO $$
DECLARE
    n integer;
BEGIN
    SELECT count(*) INTO n FROM pg_indexes
     WHERE schemaname = 'public' AND indexname = 'rag_ingestions_active_doc_uk';
    IF n < 1 THEN
        RAISE EXCEPTION 'schema assert: rag_ingestions_active_doc_uk missing';
    END IF;
END $$;

-- 1) 同一 (user, doc_id) 活跃记录只能有一条
INSERT INTO rag_ingestions (ingestion_id, doc_id, user_id, source, sha256,
                            size_bytes, stored_name)
VALUES ('__assert_ing_a__', 'u1:deadbeef', 'u1', 'a.txt', 'deadbeef', 3, 'a.txt');

DO $$
BEGIN
    BEGIN
        INSERT INTO rag_ingestions (ingestion_id, doc_id, user_id, source, sha256,
                                    size_bytes, stored_name)
        VALUES ('__assert_ing_b__', 'u1:deadbeef', 'u1', 'b.txt', 'deadbeef', 3, 'b.txt');
        RAISE EXCEPTION 'schema assert: duplicate active ingestion accepted';
    EXCEPTION WHEN unique_violation THEN
        NULL;
    END;
END $$;

-- 2) ON CONFLICT DO NOTHING 不得报错（应用侧的去重路径）
INSERT INTO rag_ingestions (ingestion_id, doc_id, user_id, source, sha256,
                            size_bytes, stored_name)
VALUES ('__assert_ing_c__', 'u1:deadbeef', 'u1', 'c.txt', 'deadbeef', 3, 'c.txt')
ON CONFLICT (COALESCE(user_id, ''), doc_id) WHERE status <> 'deleted' DO NOTHING;

DO $$
DECLARE
    n integer;
BEGIN
    SELECT count(*) INTO n FROM rag_ingestions
     WHERE COALESCE(user_id, '') = 'u1' AND doc_id = 'u1:deadbeef' AND status <> 'deleted';
    IF n <> 1 THEN
        RAISE EXCEPTION 'schema assert: expected exactly one active ingestion, got %', n;
    END IF;
END $$;

-- 3) deleted 后允许重传
UPDATE rag_ingestions SET status = 'deleted'
 WHERE ingestion_id = '__assert_ing_a__';
INSERT INTO rag_ingestions (ingestion_id, doc_id, user_id, source, sha256,
                            size_bytes, stored_name)
VALUES ('__assert_ing_d__', 'u1:deadbeef', 'u1', 'd.txt', 'deadbeef', 3, 'd.txt');

-- 4) 匿名（user_id IS NULL）同样受唯一约束
INSERT INTO rag_ingestions (ingestion_id, doc_id, user_id, source, sha256,
                            size_bytes, stored_name)
VALUES ('__assert_ing_anon_a__', 'anon:deadbeef', NULL, 'a.txt', 'deadbeef', 3, 'a.txt');
DO $$
BEGIN
    BEGIN
        INSERT INTO rag_ingestions (ingestion_id, doc_id, user_id, source, sha256,
                                    size_bytes, stored_name)
        VALUES ('__assert_ing_anon_b__', 'anon:deadbeef', NULL, 'b.txt', 'deadbeef', 3, 'b.txt');
        RAISE EXCEPTION 'schema assert: duplicate anonymous active ingestion accepted';
    EXCEPTION WHEN unique_violation THEN
        NULL;
    END;
END $$;

ROLLBACK;
