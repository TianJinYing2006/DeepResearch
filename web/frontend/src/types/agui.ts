export const AGUI_EVENT_TYPES = [
  'RUN_STARTED',
  'STEP_STARTED',
  'STEP_FINISHED',
  'STATE_DELTA',
  'DEGRADATION',
  'REPORT_DELTA',
  'REPORT_RESET',
  // 需求 36（TBD-6）：HITL 交互事件（卡片/回答/过期/追问）
  'CLARIFY_REQUESTED',
  'CLARIFY_ANSWERED',
  'CLARIFY_EXPIRED',
  'PLAN_PROPOSED',
  'PLAN_DECISION',
  'PLAN_EXPIRED',
  'FOLLOWUP_ASKED',
  'FOLLOWUP_ANSWERED',
  'RUN_FINISHED',
  'RUN_ERROR',
] as const

export type AguiEventType = (typeof AGUI_EVENT_TYPES)[number]

export interface AguiEvent {
  type: AguiEventType
  [key: string]: unknown
}

/** 结构化错误详情（P1-5）：后端 `web.backend.errors.error_payload` 的载荷。
 *
 * 前端**只认键、不解析 message 文本** —— 这是能被端到端测试锁住的契约。 */
export interface StructuredError {
  code: string
  message: string
  component: string | null
  node: string | null
  detail: string | null
  retryable: boolean
  hint: string
}

export interface RunStartedEvent extends AguiEvent {
  type: 'RUN_STARTED'
  run_id: string
  topic: string
  max_total_hops: number
  /** 后端**实际生效**的子问题数上限（Planner 分解数量的软约束） */
  max_subquestions?: number
  /** 后端**实际生效**的搜索引擎（可能与用户所选不同，如配置缺失回落默认值） */
  search_provider?: string
  /** 后端实际生效的学术检索（arXiv）开关 */
  enable_arxiv?: boolean
  /** P1-2：本次运行的墙钟时限（秒），前端据此显示剩余时间 */
  timeout_seconds?: number
}

/** 搜索源选项（来自 GET /api/options）。available=false 表示未配 key，选了会全降级。 */
export interface SearchProviderOption {
  value: string
  label: string
  available: boolean
}

/** 运行档位（P0 profile 固化）：底层参数由服务端固定，前端只提交 value。 */
export interface ProfileOption {
  value: string
  label: string
  max_total_hops: number
  max_subquestions: number
  token_budget: number
  timeout_seconds: number
  run_budget_cny: number
}

export interface RunOptions {
  search_providers: SearchProviderOption[]
  default_provider: string
  enable_arxiv_default: boolean
  max_total_hops_default: number
  max_subquestions_default: number
  /** P0：服务端档位列表（前端只展示 / 选择 value） */
  profiles?: ProfileOption[]
  /** P0：默认档位（缺省 quick） */
  default_profile?: string
  /** P1-2：后端生效的运行时限（秒） */
  run_timeout_seconds?: number
  /** P1-3：后端生效的单进程并发上限 */
  max_concurrent_runs?: number
  /** P4-A / P6-A：是否开启鉴权（前端据此决定是否展示登录门） */
  auth_required?: boolean
  /** 需求 25：错误追踪（DSN 空 = 前端不初始化） */
  sentry_dsn?: string | null
  sentry_environment?: string | null
  release?: string | null
  /** 需求 26：报告只读分享开关（隐藏分享入口） */
  share_enabled?: boolean
  /** 需求 36 H3：报告后追问开关（隐藏追问入口） */
  followup_enabled?: boolean
  /** 需求 20 P1-2：Passkey 可用性与 RP ID（前端按当前 host 再判一次） */
  webauthn?: { available: boolean; rp_id: string }
}

export interface StepFinishedEvent extends AguiEvent {
  type: 'STEP_FINISHED'
  node: string
  index: number
  duration_ms: number
  depth: number
  token_used: number
}

export interface StateDeltaEvent extends AguiEvent {
  type: 'STATE_DELTA'
  progress_added: Array<{ stage?: string; msg?: string }>
  findings_count: number
  visited_sources_count: number
  planner_events_count: number
  run_status: RunStatus
}

export interface DegradationEvent extends AguiEvent {
  type: 'DEGRADATION'
  node: string
  component: string
  reason: string
  detail: string
  fallback_action: string
}

/** 需求 35 R4（TBD-6 协议）：报告正文增量。
 *
 * `offset` = 本轮（上次 `REPORT_RESET` 之后）累计字符数——仅用于连续性诊断；
 * 预览 = reset 之后所有 delta 的**顺序拼接**。 */
export interface ReportDeltaEvent extends AguiEvent {
  type: 'REPORT_DELTA'
  text: string
  offset: number
}

/** 需求 35 R4：清空预览缓冲（流式开始 / 半截失败时发出）。
 *
 * 终局正文经 `RUN_FINISHED.result.report` **整段替换**，绝不追加到预览之后。 */
export interface ReportResetEvent extends AguiEvent {
  type: 'REPORT_RESET'
}

// ---- 需求 36：HITL 交互协议（与后端 `web/backend/hitl.py` 卡片 schema 对齐） ----

export type InteractionKind = 'clarification' | 'plan_approval'

export interface PlanSubquestion {
  id: string
  question: string
  rationale?: string
}

/** 交互卡片（`CLARIFY_REQUESTED` / `PLAN_PROPOSED` 事件载荷 = 卡片原件；
 *  快照 `interaction.card` 同形）。`allowed_actions` 为服务端允许的动作集合。 */
export interface InteractionCard {
  interaction_id: string
  kind: InteractionKind
  round: number
  max_rounds?: number
  expires_at?: string
  allowed_actions?: string[]
  content: {
    questions?: string[]
    topic?: string
    subquestions?: PlanSubquestion[]
    plan_version?: number
    revision_failed?: boolean
  }
}

/** `GET /api/research/{run_id}` 快照里的 `interaction` 字段（非等待态为 null）。 */
export interface InteractionView {
  interaction_id: string
  kind: InteractionKind
  round: number | null
  awaiting_since?: string | null
  expires_at?: string | null
  card?: InteractionCard
}

export interface ClarifyRequestedEvent extends AguiEvent, InteractionCard {
  type: 'CLARIFY_REQUESTED'
}

export interface PlanProposedEvent extends AguiEvent, InteractionCard {
  type: 'PLAN_PROPOSED'
}

/** 需求 36 H3：追问回答（`POST …/follow-up` 响应；不落库，仅前端展示）。 */
export interface FollowupAnswer {
  answer: string
  citations: string[]
  unverified_ids: string[]
  grounded: boolean
}

export interface FollowupEntry extends FollowupAnswer {
  question: string
}

export interface CitationResult {
  claim: string
  source: string
  verified: boolean
  supported: boolean
  finding_id: string
  source_type: string
  confidence: number
  note: string
  existence: boolean
  verified_relaxed: boolean
  /** 审计 P1#2：校验未完成（LLM 失败/无反馈/结论错位）——不视为通过 */
  verification_failed?: boolean
  is_meta: boolean
  /** E2：快照内逐字引文；空字符串表示当前没有可展示的原文片段。 */
  quote?: string
  /** E2：引文定位状态（located / not_found / stale）。 */
  quote_status?: string
  /** E2：引文在快照中的字符偏移，-1 表示未定位。 */
  char_start?: number
  char_end?: number
  /** E2：原文快照内容寻址 id。仅用于审计展示，不在页面中展开。 */
  snapshot_id?: string
  match_level?: string
}

export interface ResearchResult {
  report: string
  citations: CitationResult[]
  validator_stats: Record<string, unknown>
  depth: number
  visited_sources: string[]
  reflection_log: Array<Record<string, unknown>>
}

export type RunStatus = 'success' | 'degraded' | 'failed'

export interface RunFinishedEvent extends AguiEvent {
  type: 'RUN_FINISHED'
  cancelled: boolean
  /** `timeout` = P1-2 协作式超时闸触发的停止（既非完成也非用户取消） */
  stop_reason: 'completed' | 'cancelled' | 'timeout'
  run_status: RunStatus
  token_used: number
  /** LLM 成本估算（元）。口径：无 input/output 拆分，按最贵 output 单价计的**上界**。 */
  cost_estimate_cny: number
  /** #14：后端记录的总耗时（秒）——刷新回放后同样权威，不依赖前端计时器 */
  elapsed_seconds?: number
  degradation_count: number
  has_report: boolean
  /** P0-4：报告命中内容安全预检 ⇒ 正文已从事件流脱敏，等待人工复核 */
  output_under_review?: boolean
  result: ResearchResult
}

export function isAguiEvent(value: unknown): value is AguiEvent {
  if (!value || typeof value !== 'object') return false
  const type = (value as { type?: unknown }).type
  return typeof type === 'string' && (AGUI_EVENT_TYPES as readonly string[]).includes(type)
}
