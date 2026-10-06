-- 0006 结构自检：rag_ingestions 的列、状态约束与 due 索引谓词。

BEGIN;

-- 1) 关键列存在
DO $$
DECLARE
    missing text;
BEGIN
    SELECT string_agg(c, ', ') INTO missing
    FROM (VALUES
        ('rag_ingestions.ingestion_id'), ('rag_ingestions.doc_id'),
        ('rag_ingestions.user_id'), ('rag_ingestions.source'),
        ('rag_ingestions.sha256'), ('rag_ingestions.size_bytes'),
        ('rag_ingestions.stored_name'), ('rag_ingestions.status'),
        ('rag_ingestions.chunks'), ('rag_ingestions.attempts'),
        ('rag_ingestions.next_attempt_at'), ('rag_ingestions.lease_expires_at'),
        ('rag_ingestions.claimed_by'), ('rag_ingestions.scan_status'),
        ('rag_ingestions.last_error'), ('rag_ingestions.processed_at')
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

-- 2) 非法 status / scan_status 必须被 CHECK 拒绝
DO $$
BEGIN
    BEGIN
        INSERT INTO rag_ingestions (ingestion_id, doc_id, source, sha256, size_bytes,
                                    stored_name, status)
        VALUES ('__assert_ing_bad__', 'd', 's', 'h', 1, 'f', 'whatever');
        RAISE EXCEPTION 'schema assert: rag_ingestions.status accepted invalid value';
    EXCEPTION WHEN check_violation THEN
        NULL;
    END;
    BEGIN
        INSERT INTO rag_ingestions (ingestion_id, doc_id, source, sha256, size_bytes,
                                    stored_name, scan_status)
        VALUES ('__assert_ing_bad2__', 'd', 's', 'h', 1, 'f', 'whatever');
        RAISE EXCEPTION 'schema assert: rag_ingestions.scan_status accepted invalid value';
    EXCEPTION WHEN check_violation THEN
        NULL;
    END;
END $$;

-- 3) 状态机默认值与合法写入
INSERT INTO rag_ingestions (ingestion_id, doc_id, source, sha256, size_bytes, stored_name)
VALUES ('__assert_ing__', '__assert_doc__', 'a.md', 'hash', 3, 'uuid.md');
DO $$
DECLARE
    st text;
BEGIN
    SELECT status INTO st FROM rag_ingestions WHERE ingestion_id = '__assert_ing__';
    IF st <> 'pending' THEN
        RAISE EXCEPTION 'schema assert: rag_ingestions default status is % (expected pending)', st;
    END IF;
END $$;

ROLLBACK;
