export const AGUI_EVENT_TYPES = [
  'RUN_STARTED',
  'STEP_STARTED',
  'STEP_FINISHED',
  'STATE_DELTA',
  'DEGRADATION',
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

export interface RunOptions {
  search_providers: SearchProviderOption[]
  default_provider: string
  enable_arxiv_default: boolean
  max_total_hops_default: number
  max_subquestions_default: number
  /** P1-2：后端生效的运行时限（秒） */
  run_timeout_seconds?: number
  /** P1-3：后端生效的单进程并发上限 */
  max_concurrent_runs?: number
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
  is_meta: boolean
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
  cost_estimate_cny?: number
  degradation_count: number
  has_report: boolean
  result: ResearchResult
}

export interface RunErrorEvent extends StructuredError {
  type: 'RUN_ERROR'
}

export function isAguiEvent(value: unknown): value is AguiEvent {
  if (!value || typeof value !== 'object') return false
  const type = (value as { type?: unknown }).type
  return typeof type === 'string' && (AGUI_EVENT_TYPES as readonly string[]).includes(type)
}
