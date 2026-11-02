-- 0030_runs_idempotency_anon.sql —— 匿名幂等唯一性修复（需求 42 #338）
--
-- 背景：0001 的唯一索引 `(user_id, idempotency_key) WHERE idempotency_key IS NOT NULL`
-- 在 PostgreSQL 中 NULL 互不相等；而应用层查询与 `create_run` 回查都使用
-- `user_id IS NOT DISTINCT FROM %s`（匿名模式 user_id IS NULL 视为同一组）。
-- 口径不一致 ⇒ 匿名（AUTH_REQUIRED=false）并发同键可插入多行，幂等静默失效。
--
-- 修复：
--   1) 历史重复的匿名同键行（旧索引允许）保留**最新一条**为幂等权威，其余行在键上
--      追加 `#dup-<run_id>` 后缀退出幂等空间（数据/事件/报告全部保留，不改状态）；
--   2) 唯一索引改为表达式 `COALESCE(user_id, '')`，与查询口径一致；索引名保持不变
--      （`runs_idempotency_uk`），`RunStore.create_run*` 的 UniqueViolation 回查路径
--      不需改动。
--
-- 只前向、幂等可重跑（IF EXISTS / IF NOT EXISTS）；文件内不写 BEGIN/COMMIT
-- （执行器 tools/migrate.sh 已用 --single-transaction 包裹）。

DROP INDEX IF EXISTS runs_idempotency_uk;

-- 历史重复匿名键：非最新行退出幂等空间（最新 = created_at, run_id 最大者）
UPDATE runs r
   SET idempotency_key = r.idempotency_key || '#dup-' || r.run_id
 WHERE r.idempotency_key IS NOT NULL
   AND r.user_id IS NULL
   AND EXISTS (
       SELECT 1 FROM runs r2
        WHERE r2.idempotency_key = r.idempotency_key
          AND r2.user_id IS NULL
          AND (r2.created_at, r2.run_id) > (r.created_at, r.run_id)
   );

CREATE UNIQUE INDEX IF NOT EXISTS runs_idempotency_uk
    ON runs (COALESCE(user_id, ''), idempotency_key)
    WHERE idempotency_key IS NOT NULL;

COMMENT ON INDEX runs_idempotency_uk IS
    '创建幂等：匿名（user_id IS NULL）与命名用户同口径，同键仅一行；NULL 键不参与（0030）';
