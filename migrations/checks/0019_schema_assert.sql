-- 0019 结构自检：RAG 三层数据（解析快照 / 分块产物 / 版本状态机 / 全局修订号，需求 23）。
--
-- 用法（CI infra job 与本地验证共用）：
--   docker compose -f docker-compose.staging.yml exec -T postgres \
--     psql -U deepresearch -d deepresearch -v ON_ERROR_STOP=1 -f - < migrations/checks/0019_schema_assert.sql
--
-- 全部写入都在同一事务内并最终 ROLLBACK：只验行为，不落数据。

BEGIN;

-- 1) 表与关键列必须存在（含 0019 给 rag_ingestions 增加的 5 列）
DO $$
DECLARE
    missing text;
BEGIN
    SELECT string_agg(c, ', ') INTO missing
    FROM (VALUES
        ('rag_parse_snapshots.doc_id'), ('rag_parse_snapshots.block_index'),
        ('rag_parse_snapshots.kind'), ('rag_parse_snapshots.title_path'),
        ('rag_parse_snapshots.locator'), ('rag_parse_snapshots.text'),
        ('rag_parse_snapshots.created_at'),
        ('rag_chunks.doc_id'), ('rag_chunks.generation'), ('rag_chunks.chunk_index'),
        ('rag_chunks.chunk_id'), ('rag_chunks.text'), ('rag_chunks.embed_text'),
        ('rag_chunks.title_path'), ('rag_chunks.locator'), ('rag_chunks.created_at'),
        ('rag_index_generations.doc_id'), ('rag_index_generations.generation'),
        ('rag_index_generations.chunker_version'), ('rag_index_generations.embedding_model'),
        ('rag_index_generations.embedding_dim'), ('rag_index_generations.status'),
        ('rag_index_generations.chunk_count'), ('rag_index_generations.error'),
        ('rag_index_generations.created_at'), ('rag_index_generations.activated_at'),
        ('rag_revision.id'), ('rag_revision.revision'),
        ('rag_ingestions.display_name'), ('rag_ingestions.tags'),
        ('rag_ingestions.active_generation'), ('rag_ingestions.index_revision'),
        ('rag_ingestions.task')
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

-- 2) 解析快照主键 (doc_id, block_index)
INSERT INTO rag_parse_snapshots (doc_id, block_index, kind, text)
VALUES ('__assert_0019_doc__', 0, 'paragraph', 'hello'),
       ('__assert_0019_doc__', 1, 'table', 'a b');

DO $$
BEGIN
    BEGIN
        INSERT INTO rag_parse_snapshots (doc_id, block_index, kind, text)
        VALUES ('__assert_0019_doc__', 0, 'paragraph', 'duplicate');
        RAISE EXCEPTION 'schema assert: rag_parse_snapshots accepted duplicate (doc_id, block_index)';
    EXCEPTION WHEN unique_violation THEN
        NULL;
    END;
END $$;

-- 3) 分块产物：主键 (doc_id, generation, chunk_index) + UNIQUE (chunk_id)
INSERT INTO rag_chunks (doc_id, generation, chunk_index, chunk_id, text, embed_text)
VALUES ('__assert_0019_doc__', 0, 0, '__assert_0019_chunk__', 'body', 'title / body');

DO $$
BEGIN
    BEGIN
        INSERT INTO rag_chunks (doc_id, generation, chunk_index, chunk_id, text, embed_text)
        VALUES ('__assert_0019_other__', 0, 0, '__assert_0019_chunk__', 'body', 'embed');
        RAISE EXCEPTION 'schema assert: rag_chunks accepted duplicate chunk_id (global identity broken)';
    EXCEPTION WHEN unique_violation THEN
        NULL;
    END;
    BEGIN
        INSERT INTO rag_chunks (doc_id, generation, chunk_index, chunk_id, text, embed_text)
        VALUES ('__assert_0019_doc__', 0, 0, '__assert_0019_chunk2__', 'body', 'embed');
        RAISE EXCEPTION 'schema assert: rag_chunks accepted duplicate (doc_id, generation, chunk_index)';
    EXCEPTION WHEN unique_violation THEN
        NULL;
    END;
END $$;

-- 4) 版本状态机：默认 building、四值白名单、同 doc 单 active、(doc_id, generation) 唯一
INSERT INTO rag_index_generations (doc_id, generation, chunker_version, embedding_model, embedding_dim)
VALUES ('__assert_0019_doc__', 1, 'assert', 'assert', 3),
       ('__assert_0019_doc__', 2, 'assert', 'assert', 3);

DO $$
DECLARE
    n integer;
BEGIN
    SELECT count(*) INTO n FROM rag_index_generations
     WHERE doc_id = '__assert_0019_doc__' AND status = 'building';
    IF n <> 2 THEN
        RAISE EXCEPTION 'schema assert: rag_index_generations default status is not building (rows=%)', n;
    END IF;
END $$;

UPDATE rag_index_generations SET status = 'active'
 WHERE doc_id = '__assert_0019_doc__' AND generation = 1;

DO $$
BEGIN
    BEGIN
        UPDATE rag_index_generations SET status = 'active'
         WHERE doc_id = '__assert_0019_doc__' AND generation = 2;
        RAISE EXCEPTION 'schema assert: rag_index_generations accepted a second active generation for one doc';
    EXCEPTION WHEN unique_violation THEN
        NULL;
    END;
END $$;

INSERT INTO rag_index_generations (doc_id, generation, chunker_version, embedding_model,
                                   embedding_dim, status)
VALUES ('__assert_0019_doc__', 3, 'assert', 'assert', 3, 'failed'),
       ('__assert_0019_doc__', 4, 'assert', 'assert', 3, 'retired');

DO $$
BEGIN
    BEGIN
        INSERT INTO rag_index_generations (doc_id, generation, chunker_version, embedding_model,
                                           embedding_dim, status)
        VALUES ('__assert_0019_doc__', 5, 'assert', 'assert', 3, 'weird');
        RAISE EXCEPTION 'schema assert: rag_index_generations.status accepted invalid value';
    EXCEPTION WHEN check_violation THEN
        NULL;
    END;
    BEGIN
        INSERT INTO rag_index_generations (doc_id, generation, chunker_version, embedding_model,
                                           embedding_dim)
        VALUES ('__assert_0019_doc__', 2, 'assert', 'assert', 3);
        RAISE EXCEPTION 'schema assert: rag_index_generations accepted duplicate (doc_id, generation)';
    EXCEPTION WHEN unique_violation THEN
        NULL;
    END;
END $$;

-- 5) 全局修订号单行约束：id=1 存在，id<>1 被 CHECK 拒绝
DO $$
DECLARE
    n integer;
BEGIN
    SELECT count(*) INTO n FROM rag_revision WHERE id = 1 AND revision >= 0;
    IF n <> 1 THEN
        RAISE EXCEPTION 'schema assert: rag_revision singleton row (id=1) missing';
    END IF;
    BEGIN
        INSERT INTO rag_revision (id, revision) VALUES (2, 0);
        RAISE EXCEPTION 'schema assert: rag_revision accepted id <> 1';
    EXCEPTION WHEN check_violation THEN
        NULL;
    END;
END $$;

-- 6) 摄取台账新列：默认值与可写性
INSERT INTO rag_ingestions (ingestion_id, doc_id, user_id, source, sha256,
                            size_bytes, stored_name)
VALUES ('__assert_0019_ing__', '__assert_0019_doc__', '__assert_0019_user__',
        'a.txt', repeat('a', 64), 1, 'a.txt');

DO $$
DECLARE
    v_status  text;
    v_tags    jsonb;
    v_rev     bigint;
    v_task    text;
    v_display text;
    v_active  int;
BEGIN
    SELECT status, tags, index_revision, task, display_name, active_generation
      INTO v_status, v_tags, v_rev, v_task, v_display, v_active
      FROM rag_ingestions WHERE ingestion_id = '__assert_0019_ing__';
    IF v_status <> 'pending' THEN
        RAISE EXCEPTION 'schema assert: rag_ingestions default status changed -> %', v_status;
    END IF;
    IF v_tags <> '[]'::jsonb THEN
        RAISE EXCEPTION 'schema assert: rag_ingestions.tags default is not [] -> %', v_tags;
    END IF;
    IF v_rev <> 0 THEN
        RAISE EXCEPTION 'schema assert: rag_ingestions.index_revision default is not 0 -> %', v_rev;
    END IF;
    IF v_task <> 'ingest' THEN
        RAISE EXCEPTION 'schema assert: rag_ingestions.task default is not ingest -> %', v_task;
    END IF;
    IF v_display IS NOT NULL OR v_active IS NOT NULL THEN
        RAISE EXCEPTION 'schema assert: rag_ingestions display_name/active_generation should default NULL';
    END IF;
END $$;

UPDATE rag_ingestions
   SET display_name = '文档', tags = '["assert"]'::jsonb, active_generation = 1,
       index_revision = 7, task = 'reindex'
 WHERE ingestion_id = '__assert_0019_ing__';

DO $$
DECLARE
    n integer;
BEGIN
    SELECT count(*) INTO n FROM rag_ingestions
     WHERE ingestion_id = '__assert_0019_ing__'
       AND display_name = '文档' AND tags = '["assert"]'::jsonb
       AND active_generation = 1 AND index_revision = 7 AND task = 'reindex';
    IF n <> 1 THEN
        RAISE EXCEPTION 'schema assert: rag_ingestions new columns not writable (rows=%)', n;
    END IF;
END $$;

ROLLBACK;
