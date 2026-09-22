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
  stop_reason: 'completed' | 'cancelled'
  run_status: RunStatus
  token_used: number
  /** LLM 成本估算（元）。口径：无 input/output 拆分，按最贵 output 单价计的**上界**。 */
  cost_estimate_cny?: number
  degradation_count: number
  has_report: boolean
  result: ResearchResult
}

export interface RunErrorEvent extends AguiEvent {
  type: 'RUN_ERROR'
  code: string
  message: string
  node: string | null
}

export function isAguiEvent(value: unknown): value is AguiEvent {
  if (!value || typeof value !== 'object') return false
  const type = (value as { type?: unknown }).type
  return typeof type === 'string' && (AGUI_EVENT_TYPES as readonly string[]).includes(type)
}
