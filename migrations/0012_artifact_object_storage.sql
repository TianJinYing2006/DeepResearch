-- 0012_artifact_object_storage.sql —— P1-6 产物对象存储元数据
--
-- 口径依据：对象存储实践（私有桶 + 服务端生成 key + 内容 hash + 生命周期）
-- 与 docs/operations/production-readiness.md §3.2（对象存储生命周期）/§3.8（备份恢复）。
--
-- 设计：
-- - `storage='db'`（默认）沿用 `body` 存 PostgreSQL（本地 / 未配置 S3）；
-- - `storage='s3'` 时 `body=''`，正文在对象存储，本表只留 `object_key`/`sha256`/`size_bytes`；
-- - 双轨可切换：S3 写入失败自动回落 db（报告仍可用）。
--
-- 注意：执行器 tools/migrate.sh 已用 --single-transaction 包裹，本文件不得写 BEGIN/COMMIT。

ALTER TABLE run_artifacts
    ADD COLUMN storage    text NOT NULL DEFAULT 'db'
                          CHECK (storage IN ('db', 's3')),
    ADD COLUMN object_key text,
    ADD COLUMN sha256     text,
    ADD COLUMN size_bytes bigint CHECK (size_bytes IS NULL OR size_bytes >= 0);

COMMENT ON COLUMN run_artifacts.storage IS
    'P1-6：产物存储位置（db=PostgreSQL body；s3=对象存储 object_key）';
