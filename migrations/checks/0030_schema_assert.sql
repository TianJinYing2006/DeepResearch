-- 0030 结构自检：runs 幂等唯一索引覆盖匿名（user_id IS NULL）分组（需求 42 #338）。
--
-- 用法（CI infra job 与本地验证共用）：
--   docker compose -f docker-compose.staging.yml exec -T postgres \
--     psql -U deepresearch -d deepresearch -v ON_ERROR_STOP=1 -f - < migrations/checks/0030_schema_assert.sql
--
-- 全部写入都在同一事务内并最终 ROLLBACK：只验行为，不落数据。

BEGIN;

-- 1) 索引必须存在、唯一、且以 COALESCE(user_id, '') 表达式覆盖匿名分组
DO $$
DECLARE
    def text;
    is_unique boolean;
BEGIN
    SELECT pg_get_indexdef(idx.indexrelid), idx.indisunique
      INTO def, is_unique
      FROM pg_index idx
      JOIN pg_class cls ON cls.oid = idx.indexrelid
      JOIN pg_namespace ns ON ns.oid = cls.relnamespace
     WHERE ns.nspname = 'public' AND cls.relname = 'runs_idempotency_uk';
    IF def IS NULL THEN
        RAISE EXCEPTION 'schema assert: runs_idempotency_uk missing';
    END IF;
    IF NOT is_unique THEN
        RAISE EXCEPTION 'schema assert: runs_idempotency_uk is not UNIQUE -> %', def;
    END IF;
    IF position('COALESCE' in upper(def)) = 0 THEN
        RAISE EXCEPTION 'schema assert: runs_idempotency_uk does not cover anon NULL group -> %', def;
    END IF;
    IF position('IDEMPOTENCY_KEY IS NOT NULL' in upper(def)) = 0 THEN
        RAISE EXCEPTION 'schema assert: runs_idempotency_uk lost partial WHERE -> %', def;
    END IF;
END $$;

-- 2) 匿名（user_id NULL）同键第二行必须被拒（旧索引 NULL 互不冲突的缺口）
INSERT INTO runs (run_id, topic, idempotency_key)
VALUES ('__assert_0030_anon_a__', 't', '__assert_0030_key__');

DO $$
BEGIN
    BEGIN
        INSERT INTO runs (run_id, topic, idempotency_key)
        VALUES ('__assert_0030_anon_b__', 't', '__assert_0030_key__');
        RAISE EXCEPTION 'schema assert: anonymous duplicate idempotency_key accepted (NULL unique gap)';
    EXCEPTION WHEN unique_violation THEN
        NULL;
    END;
END $$;

-- 3) 匿名不同键允许；NULL 键不参与唯一约束（多行可并存）
INSERT INTO runs (run_id, topic, idempotency_key)
VALUES ('__assert_0030_anon_c__', 't', '__assert_0030_key2__'),
       ('__assert_0030_anon_d__', 't', NULL),
       ('__assert_0030_anon_e__', 't', NULL);

-- 4) 匿名键与命名用户键互不冲突（分组口径 = COALESCE(user_id,'')）
DO $$
BEGIN
    IF to_regclass('public.users') IS NOT NULL THEN
        INSERT INTO users (user_id, email, password_hash)
        VALUES ('__assert_0030_user__', '__assert_0030@example.invalid', 'x')
        ON CONFLICT DO NOTHING;
    END IF;
END $$;

INSERT INTO runs (run_id, topic, user_id, idempotency_key)
VALUES ('__assert_0030_named__', 't', '__assert_0030_user__', '__assert_0030_key__');

-- 5) 同一命名用户同键第二行仍必须被拒（表达式索引兼容 0001 语义）
DO $$
BEGIN
    BEGIN
        INSERT INTO runs (run_id, topic, user_id, idempotency_key)
        VALUES ('__assert_0030_named_dup__', 't', '__assert_0030_user__',
                '__assert_0030_key__');
        RAISE EXCEPTION 'schema assert: duplicate (user_id, idempotency_key) accepted';
    EXCEPTION WHEN unique_violation THEN
        NULL;
    END;
END $$;

-- 6) 历史重复行退出幂等空间的后缀格式（0030 的迁移口径）：带后缀键不再与原名冲突
INSERT INTO runs (run_id, topic, idempotency_key)
VALUES ('__assert_0030_dup_suffix__', 't',
        '__assert_0030_key__#dup-__assert_0030_anon_a__');

ROLLBACK;
