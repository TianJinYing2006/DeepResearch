/** REST 接口共享类型（R2：从 App / AccountPanel 内联类型收敛；行为不变）。 */

export type SessionUser = { user_id: string; email: string }

export type Quota = {
  daily_runs_used: number | null
  daily_runs_limit: number | null
  user_active_runs: number | null
  user_concurrent_limit: number
  run_budget_cny: number | null
  monthly_cost_cny: number
  monthly_budget_cny: number | null
}

export type RunBrief = {
  run_id: string
  topic: string
  status: string
  stop_reason: string | null
  created_at: string | null
  has_report: boolean
  moderation_status?: string | null
}

export type RagDoc = { doc_id?: string; source: string; chunks: number }

export type UploadStatus = 'queued' | 'uploading' | 'processing' | 'done' | 'error' | 'cancelled'

export type UploadItem = {
  id: string
  name: string
  size: number
  status: UploadStatus
  percent: number
  chunks?: number
  error?: string
  note?: string
}

/** 发起研究所需的表单参数（重试用：记住上一次**实际发起**的那组）。 */
export type LaunchParams = {
  topic: string
  instructions: string
  profile: string
}
