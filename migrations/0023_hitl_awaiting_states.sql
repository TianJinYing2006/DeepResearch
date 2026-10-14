-- 0023_hitl_awaiting_states.sql —— 需求 36 Phase 1：HITL 等待态与交互协议（2026-10-13）
--
-- 设计（需求 36 §12.2 运行协议 / §5 TBD-2、TBD-3）：
--   - 状态机**只增**两态：AWAITING_CLARIFICATION / AWAITING_PLAN_APPROVAL（等待人类输入）；
--   - 等待态不持有 worker 租约、不占执行并发槽；「活跃」与「等待」的集合口径
--     在 web/backend/persistence.py 单点定义（ACTIVE_STATUSES / AWAITING_STATUSES / OPEN_STATUSES）；
--   - 权威时间戳：awaiting_since / resumed_at —— 等待时长从时间戳差值计算，
--     **不反复顺延 timeout_at**（避免时钟漂移，§12.2）；
--   - 交互协议列：interaction_id / interaction_round / interaction_payload / interaction_answer
--     （挂起事务与回答事务都要求在**同一事务**内写状态 + 事件 + 载荷，见 store.py）；
--   - 到期规则（TBD-3）：澄清超时按空回答继续（→QUEUED，留痕）；计划审批超时
--     **不自动批准**（→TIMED_OUT + stop_reason=approval_expired）。
-- 注意：执行器 tools/migrate.sh 已用 --single-transaction 包裹，本文件不得写 BEGIN/COMMIT。

-- 1) 状态机放宽：原 9 态 + HITL 两态（旧值语义不变，只增）
ALTER TABLE runs DROP CONSTRAINT runs_status_check;
ALTER TABLE runs ADD CONSTRAINT runs_status_check CHECK (status IN (
    'CREATED','QUEUED','RUNNING','CANCEL_REQUESTED',
    'AWAITING_CLARIFICATION','AWAITING_PLAN_APPROVAL',
    'SUCCEEDED','FAILED','CANCELLED','TIMED_OUT','LOST'));

-- 2) 交互协议列
ALTER TABLE runs
    ADD COLUMN awaiting_since      timestamptz,
    ADD COLUMN awaiting_expires_at timestamptz,
    ADD COLUMN interaction_id      text,
    ADD COLUMN interaction_round   integer
        CHECK (interaction_round IS NULL OR interaction_round >= 1),
    ADD COLUMN interaction_payload jsonb,
    ADD COLUMN interaction_answer  jsonb,
    ADD COLUMN resumed_at          timestamptz;

COMMENT ON COLUMN runs.awaiting_since IS
    '需求 36：进入等待态的时间（权威时间戳；等待时长 = resumed_at - awaiting_since，不混入执行耗时）';
COMMENT ON COLUMN runs.awaiting_expires_at IS
    '需求 36：等待态 TTL 到期时间（澄清到期=空回答继续；审批到期=TIMED_OUT/approval_expired）';
COMMENT ON COLUMN runs.interaction_id IS
    '需求 36：当前待答交互的稳定 ID（回答幂等键 / 旧卡片冲突判定）';
COMMENT ON COLUMN runs.interaction_round IS
    '需求 36：交互轮次（1 起；澄清与审批各自独立计数，上限由 hitl.py 单点定义）';
COMMENT ON COLUMN runs.interaction_payload IS
    '需求 36：最近一次交互卡片载荷（interaction_id/kind/round/expires_at/allowed_actions/摘要）——SSE 重连据此重建卡片';
COMMENT ON COLUMN runs.interaction_answer IS
    '需求 36：回答载荷（命中即作为 resume payload；重复提交返回既有结果，不重复写事件）';
COMMENT ON COLUMN runs.resumed_at IS
    '需求 36：最近一次从等待态恢复（转 QUEUED）的时间';

-- 3) 等待态到期扫描（expire_awaiting_runs）与每用户等待数计数（上限策略）
CREATE INDEX runs_awaiting_expiry_idx ON runs (awaiting_expires_at)
    WHERE status IN ('AWAITING_CLARIFICATION','AWAITING_PLAN_APPROVAL');
CREATE INDEX runs_awaiting_user_idx ON runs (user_id)
    WHERE status IN ('AWAITING_CLARIFICATION','AWAITING_PLAN_APPROVAL');
