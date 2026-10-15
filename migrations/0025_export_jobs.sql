-- 0025_export_jobs.sql —— 需求 38 F2：PDF 异步导出作业（PG 权威状态；2026-10-16）
--
-- 设计（需求 38 §12.4 / §12.7 容量实测修订）：
--   - 作业事实源在 PG（Redis 仅唤醒；结果 bytes 在专用非持久化 Redis，PG 只存元数据）；
--   - 认领：FOR UPDATE SKIP LOCKED；租约心跳（默认 60s）；过期回收重排（attempt ≤ max_attempts）；
--   - 幂等：request_hash 对「进行中」作业唯一（QUEUED/RUNNING）；有效结果（未过期）直接复用；
--   - 结果元数据：result_key / result_bytes / result_hash / result_expires_at（下载时复核）；
--   - 不存报告正文/敏感内容（params 只放参数快照——本表 v1 不含正文列）。
-- 注意：执行器 tools/migrate.sh 已用 --single-transaction 包裹，本文件不得写 BEGIN/COMMIT。

CREATE TABLE export_jobs (
    job_id            text PRIMARY KEY,
    run_id            text NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    user_id           text,
    kind              text NOT NULL DEFAULT 'pdf' CHECK (kind IN ('pdf')),
    status            text NOT NULL DEFAULT 'QUEUED'
                      CHECK (status IN ('QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED', 'EXPIRED')),
    attempt           integer NOT NULL DEFAULT 0 CHECK (attempt >= 0),
    max_attempts      integer NOT NULL DEFAULT 2 CHECK (max_attempts >= 1),
    request_hash      text NOT NULL,
    lease_owner       text,
    lease_expires_at  timestamptz,
    error_code        text,
    error_detail      text,
    result_key        text,
    result_bytes      integer CHECK (result_bytes IS NULL OR result_bytes >= 0),
    result_hash       text,
    result_expires_at timestamptz,
    created_at        timestamptz NOT NULL DEFAULT now(),
    started_at        timestamptz,
    finished_at       timestamptz
);

COMMENT ON TABLE export_jobs IS
    '需求 38 F2：PDF 异步导出作业（PG 为权威状态；结果 bytes 在专用非持久化 Redis，PG 只存元数据）';

-- 幂等：同一请求指纹只允许一个进行中作业（结果复用由应用层查 find_reusable 处理）
CREATE UNIQUE INDEX export_jobs_active_uk ON export_jobs (request_hash)
    WHERE status IN ('QUEUED', 'RUNNING');
-- 认领扫描（仅 QUEUED）
CREATE INDEX export_jobs_claim_idx ON export_jobs (created_at) WHERE status = 'QUEUED';
-- 租约回收扫描（仅 RUNNING）
CREATE INDEX export_jobs_lease_idx ON export_jobs (lease_expires_at) WHERE status = 'RUNNING';
-- 按 run 查询（状态查询 / 下载授权）
CREATE INDEX export_jobs_run_idx ON export_jobs (run_id, created_at DESC);
