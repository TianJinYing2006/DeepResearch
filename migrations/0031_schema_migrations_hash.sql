-- 0031_schema_migrations_hash.sql —— 迁移记录内容指纹（需求 42 #339）
--
-- 目的：tools/migrate.sh 重跑时校验「已应用迁移文件」未被篡改/改名（审计 #339：
-- 旧实现只记 version/applied_at，文件被改动后仍静默 skip）。只前向、幂等。
--
-- 说明：migrate.sh 的 bootstrap 也会执行同样的 ADD COLUMN IF NOT EXISTS，保证独立
-- 运行器在「0028–0030 先于 0031 应用」等任意顺序下都能写入指纹；本文件是**规范的
-- DDL 记录**（新环境/审计核对用）。历史行（sha256 IS NULL）由 migrate.sh 在下次
-- 重跑时按当前文件内容回填，之后按 hash 校验。
--
-- 注意：执行器 tools/migrate.sh 已用 --single-transaction 包裹，本文件不得写 BEGIN/COMMIT。

ALTER TABLE schema_migrations ADD COLUMN IF NOT EXISTS sha256 text;

COMMENT ON COLUMN schema_migrations.sha256 IS
    '已应用迁移文件内容的 SHA-256（重跑校验；NULL=引入指纹前应用的历史行，下次重跑回填）';
