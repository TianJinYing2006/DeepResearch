-- 0017_rag_active_unique.sql —— P0-8b 上传去重并发兜底
--
-- 背景（审计 P0-11）：上传链路是「find_ingestion_by_doc → create_ingestion」两步，
-- 0006 只有普通索引 ⇒ 两个并发请求可能都查不到、都插入，造成重复解析 / 重复
-- embedding 计费 / 状态互相覆盖。本迁移补数据库级唯一约束：
--
--   (COALESCE(user_id,''), doc_id) WHERE status <> 'deleted'
--
-- 语义：同一用户（含匿名 NULL）的同一内容只允许一条**活跃**摄取记录；
-- `deleted` 不占用唯一位，删除后可重新上传（新记录、新 hash 版本）。
-- 应用侧改用 `INSERT ... ON CONFLICT ... DO NOTHING RETURNING *`（store.create_ingestion）。
--
-- 历史数据中若已存在并发写入的重复活跃记录，保留最新一条，其余标记 deleted
-- （quarantine 文件由保留期清扫/账号注销流程按 deleted 记录回收；last_error 留痕原因）。
--
-- 注意：执行器 tools/migrate.sh 已用 --single-transaction 包裹，本文件不得写 BEGIN/COMMIT。

UPDATE rag_ingestions
   SET status = 'deleted',
       last_error = COALESCE(last_error, 'dedupe_0017: duplicate active doc_id'),
       lease_expires_at = NULL,
       claimed_by = NULL,
       updated_at = now()
 WHERE ingestion_id IN (
     SELECT ingestion_id FROM (
         SELECT ingestion_id,
                row_number() OVER (
                    PARTITION BY COALESCE(user_id, ''), doc_id
                    ORDER BY created_at DESC, ingestion_id DESC
                ) AS rn
           FROM rag_ingestions
          WHERE status <> 'deleted'
     ) ranked
     WHERE rn > 1
 );

CREATE UNIQUE INDEX rag_ingestions_active_doc_uk
    ON rag_ingestions (COALESCE(user_id, ''), doc_id)
    WHERE status <> 'deleted';

COMMENT ON INDEX rag_ingestions_active_doc_uk IS
    'P0-8b：同一用户同一 doc_id 仅一条活跃摄取（deleted 后可重传）；ON CONFLICT DO NOTHING 的冲突目标';
