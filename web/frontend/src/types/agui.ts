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
