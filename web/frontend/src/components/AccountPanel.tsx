import { useCallback, useEffect, useState } from 'react'
import { ReportView } from './ReportView'

type SessionUser = { user_id: string; email: string }

type Quota = {
  daily_runs_used: number | null
  daily_runs_limit: number | null
  user_active_runs: number | null
  user_concurrent_limit: number
  run_budget_cny: number | null
  monthly_cost_cny: number
  monthly_budget_cny: number | null
}

type RunBrief = {
  run_id: string
  topic: string
  status: string
  stop_reason: string | null
  created_at: string | null
  has_report: boolean
}

type RagDoc = { source: string; chunks: number }

function csrfHeaders(): Record<string, string> {
  const match = document.cookie.match(/(?:^|;\s*)dr_csrf=([^;]+)/)
  return match ? { 'X-CSRF-Token': decodeURIComponent(match[1]) } : {}
}

async function readError(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: unknown }
    const detail = body.detail
    if (detail && typeof detail === 'object' && 'message' in detail) {
      return String((detail as { message?: string }).message ?? '')
    }
    if (typeof detail === 'string') return detail
  } catch {
    /* 非 JSON 响应 */
  }
  return `HTTP ${response.status}`
}

const STATUS_LABELS: Record<string, string> = {
  SUCCEEDED: '已完成',
  FAILED: '失败',
  CANCELLED: '已取消',
  TIMED_OUT: '已超时',
  LOST: '已失联',
  RUNNING: '运行中',
  QUEUED: '排队中',
  CANCEL_REQUESTED: '正在停止',
  CREATED: '已创建',
}

type Props = {
  authRequired: boolean
  activeRunId: string | null
  running: boolean
}

/**
 * P6-A 账号面板：登录/注册门、账号条、配额、历史任务、知识库上传。
 *
 * - 鉴权开启（authRequired）且未登录时渲染全屏登录门；
 * - 未开启鉴权时保持匿名可用（历史/上传仍可用，配额由后端按匿名口径返回）。
 */
export default function AccountPanel({ authRequired, activeRunId, running }: Props) {
  const [user, setUser] = useState<SessionUser | null>(null)
  const [checked, setChecked] = useState(false)
  const [quota, setQuota] = useState<Quota | null>(null)
  const [docs, setDocs] = useState<RagDoc[] | null>(null)
  const [docsError, setDocsError] = useState('')
  const [historyOpen, setHistoryOpen] = useState(false)
  const [history, setHistory] = useState<RunBrief[] | null>(null)
  const [historyError, setHistoryError] = useState('')
  const [preview, setPreview] = useState<{ runId: string; markdown: string } | null>(null)
  const [previewError, setPreviewError] = useState('')
  const [uploadState, setUploadState] = useState('')
  const [busy, setBusy] = useState(false)
  const [mode, setMode] = useState<'login' | 'register'>('login')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [inviteCode, setInviteCode] = useState('')
  const [authError, setAuthError] = useState('')

  const loadSession = useCallback(async () => {
    try {
      const response = await fetch('/api/auth/session')
      if (response.ok) {
        const body = (await response.json()) as { user: SessionUser }
        setUser(body.user)
        setChecked(true)
        return
      }
    } catch {
      /* 网络失败按未登录处理 */
    }
    setUser(null)
    setChecked(true)
  }, [])

  const refreshSideData = useCallback(async () => {
    const [quotaResp, docsResp] = await Promise.all([fetch('/api/quota'), fetch('/api/rag/docs')])
    if (quotaResp.ok) setQuota((await quotaResp.json()) as Quota)
    else setQuota(null)
    if (docsResp.ok) {
      const body = (await docsResp.json()) as { docs: RagDoc[] }
      setDocs(body.docs)
      setDocsError('')
    } else {
      setDocs(null)
      setDocsError(await readError(docsResp))
    }
  }, [])

  useEffect(() => {
    void loadSession()
  }, [loadSession])

  useEffect(() => {
    if (checked && user) void refreshSideData()
    if (checked && !user) void refreshSideData()
  }, [checked, user, refreshSideData, activeRunId, running])

  async function submitAuth(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setBusy(true)
    setAuthError('')
    try {
      const payload =
        mode === 'login'
          ? { email, password }
          : { email, password, invite_code: inviteCode }
      const response = await fetch(`/api/auth/${mode === 'login' ? 'login' : 'register'}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })
      if (!response.ok) {
        setAuthError(await readError(response))
        return
      }
      const body = (await response.json()) as { user: SessionUser }
      setUser(body.user)
      setPassword('')
      setInviteCode('')
      void refreshSideData()
    } catch {
      setAuthError('网络错误，请重试')
    } finally {
      setBusy(false)
    }
  }

  async function logout() {
    await fetch('/api/auth/logout', { method: 'POST' })
    setUser(null)
    setQuota(null)
    setHistory(null)
    setHistoryOpen(false)
  }

  async function toggleHistory() {
    const next = !historyOpen
    setHistoryOpen(next)
    if (!next) return
    setHistoryError('')
    setHistory(null)
    try {
      const response = await fetch('/api/runs')
      if (!response.ok) {
        setHistoryError(await readError(response))
        return
      }
      const body = (await response.json()) as { runs: RunBrief[] }
      setHistory(body.runs)
    } catch {
      setHistoryError('网络错误，请重试')
    }
  }

  async function openReport(runId: string) {
    setPreviewError('')
    setPreview(null)
    const response = await fetch(`/api/research/${runId}/report?format=md`)
    if (!response.ok) {
      setPreviewError(await readError(response))
      return
    }
    setPreview({ runId, markdown: await response.text() })
  }

  async function uploadFile(file: File) {
    setUploadState('上传中…')
    try {
      const form = new FormData()
      form.append('file', file)
      const response = await fetch('/api/rag/ingest', {
        method: 'POST',
        headers: csrfHeaders(),
        body: form,
      })
      if (!response.ok) {
        setUploadState(await readError(response))
        return
      }
      const body = (await response.json()) as { source: string; chunks: number }
      setUploadState(`已摄取 ${body.source}（${body.chunks} 块）`)
      void refreshSideData()
    } catch {
      setUploadState('网络错误，请重试')
    }
  }

  const quotaLine = quota
    ? [
        quota.daily_runs_used !== null && quota.daily_runs_limit
          ? `今日 ${quota.daily_runs_used}/${quota.daily_runs_limit}`
          : null,
        quota.user_active_runs !== null ? `并发 ${quota.user_active_runs}/${quota.user_concurrent_limit}` : null,
        quota.monthly_budget_cny
          ? `本月 ¥${quota.monthly_cost_cny.toFixed(2)}/¥${quota.monthly_budget_cny}`
          : null,
      ]
        .filter(Boolean)
        .join(' · ')
    : ''

  if (authRequired && checked && !user) {
    return (
      <div
        className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4 backdrop-blur-sm"
        data-testid="auth-gate"
      >
        <form onSubmit={(event) => void submitAuth(event)} className="surface-card w-full max-w-md p-6">
          <h2 className="text-lg font-semibold text-emerald-50">
            {mode === 'login' ? '登录 DeepResearch' : '邀请制注册'}
          </h2>
          <p className="mt-1 text-xs text-emerald-100/60">
            {mode === 'login' ? '使用邮箱与密码登录' : '需要一次性邀请码（管理员通过 CLI 生成）'}
          </p>
          <label className="field-label mt-5" htmlFor="auth-email">邮箱</label>
          <input id="auth-email" className="field-control" type="email" autoComplete="email"
                 value={email} onChange={(event) => setEmail(event.target.value)} required />
          <label className="field-label mt-4" htmlFor="auth-password">密码</label>
          <input id="auth-password" className="field-control" type="password"
                 autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
                 value={password} onChange={(event) => setPassword(event.target.value)}
                 minLength={10} required />
          {mode === 'register' && (
            <>
              <label className="field-label mt-4" htmlFor="auth-invite">邀请码</label>
              <input id="auth-invite" className="field-control" value={inviteCode}
                     onChange={(event) => setInviteCode(event.target.value)} required />
            </>
          )}
          {authError && (
            <p className="mt-4 text-sm text-rose-300" data-testid="auth-error">{authError}</p>
          )}
          <button type="submit" className="primary-button mt-6 w-full" disabled={busy}>
            {busy ? '提交中…' : mode === 'login' ? '登录' : '注册并登录'}
          </button>
          <button type="button" className="mt-3 w-full text-xs text-emerald-200/70 hover:text-emerald-100"
                  onClick={() => { setMode(mode === 'login' ? 'register' : 'login'); setAuthError('') }}>
            {mode === 'login' ? '有邀请码？去注册' : '已有账号？去登录'}
          </button>
        </form>
      </div>
    )
  }

  return (
    <div className="flex flex-wrap items-center justify-end gap-2 text-xs" data-testid="account-panel">
      {quotaLine && (
        <span className="rounded-full border border-white/10 bg-white/[0.03] px-3 py-1 text-emerald-100/70"
              data-testid="quota-chip">
          {quotaLine}
        </span>
      )}
      <button type="button" className="rounded-full border border-white/10 px-3 py-1 text-emerald-100/80 hover:border-emerald-300/40"
              onClick={() => void toggleHistory()} data-testid="history-toggle">
        历史任务
      </button>
      <label className="cursor-pointer rounded-full border border-white/10 px-3 py-1 text-emerald-100/80 hover:border-emerald-300/40">
        上传文档
        <input type="file" accept=".pdf,.docx,.md,.markdown,.txt" className="hidden"
               data-testid="rag-upload-input"
               onChange={(event) => {
                 const file = event.target.files?.[0]
                 if (file) void uploadFile(file)
                 event.target.value = ''
               }} />
      </label>
      {docs && <span className="text-emerald-100/60">知识库 {docs.length} 篇</span>}
      {!docs && docsError && <span className="text-amber-200/70">知识库不可用</span>}
      {uploadState && <span className="text-emerald-100/70" data-testid="upload-state">{uploadState}</span>}
      {user ? (
        <>
          <span className="text-emerald-100/70" data-testid="account-email">{user.email}</span>
          <button type="button" className="text-emerald-100/60 hover:text-emerald-100"
                  onClick={() => void logout()} data-testid="logout-button">退出</button>
        </>
      ) : (
        <span className="text-emerald-100/50">未登录（本地模式）</span>
      )}

      {historyOpen && (
        <div className="surface-card-muted mt-2 w-full p-3" data-testid="history-panel">
          {historyError && <p className="text-amber-200/80" data-testid="history-error">{historyError}</p>}
          {!historyError && history === null && <p className="text-emerald-100/60">加载中…</p>}
          {!historyError && history && history.length === 0 && <p className="text-emerald-100/60">暂无历史任务</p>}
          {!historyError && history && history.length > 0 && (
            <ul className="space-y-2">
              {history.map((item) => (
                <li key={item.run_id} className="flex flex-wrap items-center justify-between gap-2 text-emerald-50/90">
                  <span className="truncate">
                    <code className="mr-2 text-[11px] text-emerald-200/70">{item.run_id}</code>
                    {item.topic}
                  </span>
                  <span className="flex items-center gap-2 text-[11px] text-emerald-100/60">
                    {STATUS_LABELS[item.status] ?? item.status}
                    {item.created_at && <span>{item.created_at.replace('T', ' ').slice(0, 16)}</span>}
                    {item.has_report && (
                      <button type="button" className="underline hover:text-emerald-100"
                              onClick={() => void openReport(item.run_id)}>查看报告</button>
                    )}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {(preview || previewError) && (
        <div className="fixed inset-0 z-50 flex justify-center overflow-y-auto bg-black/70 p-4 backdrop-blur-sm"
             data-testid="history-preview">
          <div className="surface-card my-6 w-full max-w-3xl p-6">
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-semibold text-emerald-50">历史报告 {preview?.runId}</h3>
              <button type="button" className="text-xs text-emerald-200/70 hover:text-emerald-100"
                      onClick={() => { setPreview(null); setPreviewError('') }}>关闭</button>
            </div>
            {previewError && <p className="mt-3 text-sm text-rose-300">{previewError}</p>}
            {preview && (
              <div className="mt-4">
                <ReportView report={preview.markdown} />
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
