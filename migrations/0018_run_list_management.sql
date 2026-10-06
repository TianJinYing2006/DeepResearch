-- 0018_run_list_management.sql —— 需求 22 任务列表增强（搜索 / 置顶 / 软归档）
--
-- 背景（需求 21 批次 A1 / 需求 22）：历史任务只有「状态筛选 + 分页」，缺关键词搜索
-- 与整理能力。本迁移补：
--   1) pg_trgm 扩展 + topic GIN 索引：`q` 模糊搜索（ILIKE %q%）在万级 run 下保持可用；
--   2) pinned_at / archived_at：置顶与**软归档**（仅影响默认列表可见性；
--      数据仍按保留期政策清扫）——不引入 deleted_at，避免与审计 / 用量账本口径冲突。
--
-- 说明：archive 仅改变列表可见性，不改变 run 状态机；进行中任务禁止归档由 API 层判定。
-- 注意：执行器 tools/migrate.sh 已用 --single-transaction 包裹，本文件不得写 BEGIN/COMMIT。

CREATE EXTENSION IF NOT EXISTS pg_trgm;

ALTER TABLE runs ADD COLUMN IF NOT EXISTS pinned_at   timestamptz;
ALTER TABLE runs ADD COLUMN IF NOT EXISTS archived_at timestamptz;

CREATE INDEX IF NOT EXISTS runs_topic_trgm_idx
    ON runs USING gin (topic gin_trgm_ops);

COMMENT ON COLUMN runs.pinned_at IS '需求 22：置顶时间（NULL=未置顶）；列表排序 pinned_at DESC NULLS LAST';
COMMENT ON COLUMN runs.archived_at IS '需求 22：软归档时间（NULL=未归档）；默认列表过滤 archived_at IS NULL';
