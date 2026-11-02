-- 0029_report_share_active_unique.sql —— 需求 26 分享并发兜底（审计 #330）
--
-- 背景：0021 的注释承诺「一 run 一条活跃链接」，但只建了普通部分索引
-- report_shares_run_active_idx；store.create_report_share 是「UPDATE 旧活跃 → INSERT」
-- 两条语句，READ COMMITTED 下两个并发创建都先 UPDATE 0 行、再各自 INSERT
-- ⇒ 同一 run 出现 2 条活跃链接（旧 token 仍可通过 /s/<token> 读取，revoke 后不收敛）。
--
-- 本迁移做两件事：
--   1) 清理历史重复：同一 run 下除「最新一条」外的活跃行置 revoked_at = now()；
--   2) 建**唯一**部分索引：并发创建由数据库最终拦截（应用侧另改为先锁 runs 行）。
--
-- 注意：执行器 tools/migrate.sh 已用 --single-transaction 包裹，本文件不得写 BEGIN/COMMIT。
-- 只前向、可重跑：清理幂等（二次执行更新 0 行），索引 IF NOT EXISTS。

UPDATE report_shares AS stale
   SET revoked_at = now()
 WHERE stale.revoked_at IS NULL
   AND EXISTS (
       SELECT 1
         FROM report_shares AS newer
        WHERE newer.run_id = stale.run_id
          AND newer.revoked_at IS NULL
          AND (newer.created_at, newer.share_id) > (stale.created_at, stale.share_id)
   );

CREATE UNIQUE INDEX IF NOT EXISTS report_shares_run_active_uk
    ON report_shares (run_id) WHERE revoked_at IS NULL;

COMMENT ON INDEX report_shares_run_active_uk IS
    '需求 26/#330：一 run 仅一条活跃分享（并发创建数据库级兜底；应用侧另锁 runs 行）';
