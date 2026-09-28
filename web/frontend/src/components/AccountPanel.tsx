import { useCallback, useEffect, useRef, useState } from 'react'
import { csrfHeaders, errorMessageFromBody, readErrorMessage } from '../lib/api'
import { formatBytes, formatCny, formatDateTime } from '../lib/format'
import type { Quota, RagDoc, RunBrief, SessionUser, UploadItem } from '../types/api'
import Modal from './Modal'
import { ReportView } from './ReportView'

type UploadHttpResult = { status: number; body: unknown }

/** 上传字节进度只有 XHR 能拿到（fetch 不暴露 upload progress）。 */
function xhrUpload(file: File, onProgress: (percent: number) => void): Promise<UploadHttpResult> {
  return new Promise((resolve) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', '/api/rag/ingest')
    for (const [key, value] of Object.entries(csrfHeaders())) {
      xhr.setRequestHeader(key, value)
    }
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable && event.total > 0) {
        onProgress(Math.min(100, Math.round((event.loaded / event.total) * 100)))
      }
    }
    xhr.onload = () => {
      let body: unknown = null
      try {
        body = JSON.parse(xhr.responseText)
      } catch {
        body = null
      }
      resolve({ status: xhr.status, body })
    }
    xhr.onerror = () => resolve({ status: 0, body: null })
    xhr.onabort = () => resolve({ status: 0, body: null })
    const form = new FormData()
    form.append('file', file)
    xhr.send(form)
  })
}

function uploadLabel(item: UploadItem): string {
  switch (item.status) {
    case 'queued':
      return '排队中'
    case 'uploading':
      return `上传中 ${item.percent}%`
    case 'processing':
      return item.note ?? '处理中…'
    case 'done':
      return item.chunks ? `已入库（${item.chunks} 块）` : '已入库'
    case 'error':
      return `失败：${item.error ?? '未知错误'}`
    default:
      return item.status
  }
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

const HISTORY_PAGE_SIZE = 10

/** P6-B：邀请链接 `?invite=CODE`（可复制给被邀请人，打开即进入注册并预填）。 */
function inviteFromLocation(): string {
  try {
    return new URLSearchParams(window.location.search).get('invite')?.trim() ?? ''
  } catch {
    return ''
  }
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
  const [uploads, setUploads] = useState<UploadItem[]>([])
  const [kbOpen, setKbOpen] = useState(false)
  const uploadFilesRef = useRef<Map<string, File>>(new Map())
  const [busy, setBusy] = useState(false)
  const inviteFromUrl = useRef(inviteFromLocation()).current
  const [mode, setMode] = useState<'login' | 'register'>(inviteFromUrl ? 'register' : 'login')
  const [inviteOpen, setInviteOpen] = useState(Boolean(inviteFromUrl))
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [inviteCode, setInviteCode] = useState(inviteFromUrl)
  const [authError, setAuthError] = useState('')
  const [historyStatus, setHistoryStatus] = useState('')
  const [historyOffset, setHistoryOffset] = useState(0)
  const [historyHasMore, setHistoryHasMore] = useState(false)
  const [historyLoadingMore, setHistoryLoadingMore] = useState(false)
  const [legal, setLegal] = useState<{ doc: string; markdown: string } | null>(null)
  const [legalError, setLegalError] = useState('')

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
      setDocsError(await readErrorMessage(docsResp))
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
        setAuthError(await readErrorMessage(response))
        return
      }
      const body = (await response.json()) as { user: SessionUser }
      setUser(body.user)
      setPassword('')
      setInviteCode('')
      setInviteOpen(false)
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

  async function loadHistory(reset: boolean, statusOverride?: string) {
    const status = statusOverride !== undefined ? statusOverride : historyStatus
    const nextOffset = reset ? 0 : historyOffset
    const params = new URLSearchParams({ limit: String(HISTORY_PAGE_SIZE), offset: String(nextOffset) })
    if (status) params.set('status', status)
    setHistoryLoadingMore(!reset)
    try {
      const response = await fetch(`/api/runs?${params.toString()}`)
      if (!response.ok) {
        setHistoryError(await readErrorMessage(response))
        return
      }
      const body = (await response.json()) as { runs: RunBrief[] }
      setHistoryError('')
      setHistory((previous) => (reset ? body.runs : [...(previous ?? []), ...body.runs]))
      setHistoryOffset(nextOffset + body.runs.length)
      setHistoryHasMore(body.runs.length === HISTORY_PAGE_SIZE)
    } catch {
      setHistoryError('网络错误，请重试')
    } finally {
      setHistoryLoadingMore(false)
    }
  }

  async function toggleHistory() {
    const next = !historyOpen
    setHistoryOpen(next)
    if (!next) return
    setHistory(null)
    await loadHistory(true)
  }

  async function openReport(runId: string) {
    setPreviewError('')
    setPreview(null)
    const response = await fetch(`/api/research/${runId}/report?format=md`)
    if (!response.ok) {
      setPreviewError(await readErrorMessage(response))
      return
    }
    setPreview({ runId, markdown: await response.text() })
  }

  async function openLegal(doc: 'privacy' | 'terms') {
    setLegalError('')
    setLegal({ doc, markdown: '' })
    try {
      const response = await fetch(`/api/legal/${doc}`)
      if (!response.ok) {
        setLegalError(await readErrorMessage(response))
        return
      }
      const body = (await response.json()) as { markdown: string }
      setLegal({ doc, markdown: body.markdown })
    } catch {
      setLegalError('网络错误，请重试')
    }
  }

  async function deleteAccount() {
    const password = window.prompt('注销将删除账号与会话、并尽力清理知识库向量（历史任务匿名保留）。请输入密码确认：')
    if (!password) return
    try {
      const response = await fetch('/api/auth/account', {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json', ...csrfHeaders() },
        body: JSON.stringify({ password }),
      })
      if (!response.ok) {
        setUploadState(await readErrorMessage(response))
        return
      }
      const body = (await response.json()) as { rag_cleanup: string }
      setUser(null)
      setQuota(null)
      setHistory(null)
      setUploadState(`账号已注销（知识库清理：${body.rag_cleanup}）`)
    } catch {
      setUploadState('网络错误，请重试')
    }
  }

  async function pollIngestion(ingestionId: string, attempts = 30): Promise<{
    status: string; chunks: number; source: string; error: string | null
  }> {
    // P0-8b：异步摄取 —— 每秒轮询直到终态（最长约 30s，之后提示稍后刷新）
    for (let index = 0; index < attempts; index += 1) {
      await new Promise((resolve) => setTimeout(resolve, 1000))
      try {
        const response = await fetch(`/api/rag/ingestions/${ingestionId}`)
        if (!response.ok) continue
        const body = (await response.json()) as {
          status: string; chunks: number; source: string; error: string | null
        }
        if (body.status === 'ready' || body.status === 'rejected') return body
      } catch {
        /* 网络抖动继续轮询 */
      }
    }
    return { status: 'timeout', chunks: 0, source: '', error: null }
  }

  function updateUpload(id: string, patch: Partial<UploadItem>) {
    setUploads((previous) => previous.map((item) => (item.id === id ? { ...item, ...patch } : item)))
  }

  async function processUpload(item: UploadItem) {
    const file = uploadFilesRef.current.get(item.id)
    if (!file) return
    updateUpload(item.id, { status: 'uploading', percent: 0 })
    const { status, body } = await xhrUpload(file, (percent) => updateUpload(item.id, { percent }))
    if (status === 0) {
      updateUpload(item.id, { status: 'error', error: '网络错误，请重试' })
      setUploadState('网络错误，请重试')
      return
    }
    if (status < 200 || status >= 300) {
      const message = errorMessageFromBody(body, status)
      updateUpload(item.id, { status: 'error', error: message })
      setUploadState(message)
      return
    }
    const parsed = (body ?? {}) as { source?: string; chunks?: number; ingestion_id?: string }
    if (parsed.ingestion_id) {
      // P0-8b：异步摄取协议只有粗粒度状态 ⇒ 不显示百分比，只做不确定态动画
      updateUpload(item.id, { status: 'processing', percent: 100, chunks: undefined })
      const final = await pollIngestion(parsed.ingestion_id)
      if (final.status === 'ready') {
        updateUpload(item.id, { status: 'done', chunks: final.chunks })
        setUploadState(`已摄取 ${final.source || item.name}（${final.chunks} 块）`)
      } else if (final.status === 'rejected') {
        updateUpload(item.id, { status: 'error', error: final.error ?? '已拒绝' })
        setUploadState(`${final.source || item.name} 处理失败：${final.error ?? '已拒绝'}`)
      } else {
        updateUpload(item.id, { status: 'processing', note: '仍在处理中，稍后刷新查看' })
      }
    } else {
      updateUpload(item.id, { status: 'done', percent: 100, chunks: parsed.chunks ?? 0 })
      setUploadState(`已摄取 ${parsed.source ?? item.name}（${parsed.chunks ?? 0} 块）`)
    }
    uploadFilesRef.current.delete(item.id)
  }

  async function handleFiles(fileList: FileList) {
    const files = Array.from(fileList)
    if (!files.length) return
    setKbOpen(true)
    const items: UploadItem[] = files.map((file) => ({
      id: `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`,
      name: file.name,
      size: file.size,
      status: 'queued',
      percent: 0,
    }))
    items.forEach((item, index) => uploadFilesRef.current.set(item.id, files[index]))
    setUploads((previous) => [...previous, ...items])
    setUploadState('')
    for (const item of items) {
      await processUpload(item)
    }
    await refreshSideData()
  }

  async function toggleKnowledgeBase() {
    const next = !kbOpen
    setKbOpen(next)
    if (next) await refreshSideData()
  }

  const quotaLine = quota
    ? [
        quota.daily_runs_used !== null && quota.daily_runs_limit
          ? `今日 ${quota.daily_runs_used}/${quota.daily_runs_limit}`
          : null,
        quota.user_active_runs !== null ? `并发 ${quota.user_active_runs}/${quota.user_concurrent_limit}` : null,
        quota.monthly_budget_cny
          ? `本月 ${formatCny(quota.monthly_cost_cny)}/${formatCny(quota.monthly_budget_cny)}`
          : null,
      ]
        .filter(Boolean)
        .join(' · ')
    : ''

  const legalModal = legal ? (
    <Modal
      onClose={() => { setLegal(null); setLegalError('') }}
      labelledBy="legal-title"
      testId="legal-modal"
      overlayClassName="z-[60] flex justify-center overflow-y-auto bg-black/75 p-4 backdrop-blur-sm"
      panelClassName="surface-card my-6 w-full max-w-3xl p-6"
    >
      <div className="flex items-center justify-between">
        <h3 id="legal-title" className="text-sm font-semibold text-emerald-50">
          {legal.doc === 'privacy' ? '隐私政策' : '用户协议'}
        </h3>
        <button type="button" className="text-xs text-emerald-200/70 hover:text-emerald-100"
                data-testid="legal-close"
                onClick={() => { setLegal(null); setLegalError('') }}>关闭</button>
      </div>
      {legalError && <p role="alert" className="mt-3 text-sm text-rose-300">{legalError}</p>}
      {!legalError && !legal.markdown && <p className="mt-3 text-sm text-emerald-100/60">加载中…</p>}
      {!legalError && legal.markdown && (
        <div className="mt-4">
          <ReportView report={legal.markdown} />
        </div>
      )}
    </Modal>
  ) : null

  if (authRequired && checked && !user) {
    // 登录门不可关闭（dismissible=false）：只保留 dialog 语义与焦点管理
    return (
      <Modal
        onClose={() => {}}
        labelledBy="auth-title"
        testId="auth-gate"
        dismissible={false}
        overlayClassName="z-50 flex items-center justify-center bg-black/70 p-4 backdrop-blur-sm"
        panelClassName="surface-card w-full max-w-md p-6"
      >
        <form onSubmit={(event) => void submitAuth(event)}>
          <h2 id="auth-title" className="text-lg font-semibold text-emerald-50">
            {mode === 'login' ? '登录 DeepResearch' : '邀请制注册'}
          </h2>
          <p className="mt-1 text-xs text-emerald-100/60">
            {mode === 'login' ? '使用邮箱与密码登录' : '需要一次性邀请码（管理员通过 CLI 生成）'}
          </p>
          <label className="field-label mt-5" htmlFor="auth-email">邮箱</label>
          <input id="auth-email" name="email" className="field-control" type="email" autoComplete="email"
                 aria-invalid={authError ? true : undefined}
                 aria-describedby={authError ? 'auth-error' : undefined}
                 value={email} onChange={(event) => setEmail(event.target.value)} required />
          <label className="field-label mt-4" htmlFor="auth-password">密码</label>
          <input id="auth-password" name="password" className="field-control" type="password"
                 autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
                 aria-invalid={authError ? true : undefined}
                 aria-describedby={authError ? 'auth-error' : undefined}
                 value={password} onChange={(event) => setPassword(event.target.value)}
                 minLength={10} required />
          {mode === 'register' && (
            <>
              <label className="field-label mt-4" htmlFor="auth-invite">邀请码</label>
              <input id="auth-invite" name="invite_code" className="field-control" value={inviteCode}
                     onChange={(event) => setInviteCode(event.target.value)} required />
            </>
          )}
          {authError && (
            <p id="auth-error" role="alert" className="mt-4 text-sm text-rose-300"
               data-testid="auth-error">{authError}</p>
          )}
          <button type="submit" className="primary-button mt-6 w-full" disabled={busy}>
            {busy ? '提交中…' : mode === 'login' ? '登录' : '注册并登录'}
          </button>
          <button type="button" className="mt-3 w-full text-xs text-emerald-200/70 hover:text-emerald-100"
                  onClick={() => { setMode(mode === 'login' ? 'register' : 'login'); setAuthError('') }}>
            {mode === 'login' ? '有邀请码？去注册' : '已有账号？去登录'}
          </button>
          <p className="mt-3 text-center text-[11px] text-emerald-100/50">
            注册即表示同意
            <button type="button" className="mx-1 underline hover:text-emerald-100"
                    onClick={() => void openLegal('terms')}>用户协议</button>
            与
            <button type="button" className="mx-1 underline hover:text-emerald-100"
                    onClick={() => void openLegal('privacy')}>隐私政策</button>
          </p>
          {legalModal}
        </form>
      </Modal>
    )
  }

  return (
    <div className="flex flex-wrap items-center justify-start gap-2 text-xs sm:justify-end" data-testid="account-panel">
      {quotaLine && (
        <span className="rounded-full border border-white/10 bg-white/[0.03] px-3 py-1 tabular-nums text-emerald-100/70"
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
        <input type="file" accept=".pdf,.docx,.md,.markdown,.txt" multiple className="hidden"
               data-testid="rag-upload-input"
               onChange={(event) => {
                 if (event.target.files?.length) void handleFiles(event.target.files)
                 event.target.value = ''
               }} />
      </label>
      <button type="button" className="rounded-full border border-white/10 px-3 py-1 text-emerald-100/80 hover:border-emerald-300/40"
              onClick={() => void toggleKnowledgeBase()} data-testid="kb-toggle">
        知识库 {docs ? `${docs.length} 篇` : docsError ? '不可用' : '…'}
      </button>
      {uploadState && (
        <span className="text-emerald-100/70" role="status" aria-live="polite"
              data-testid="upload-state">{uploadState}</span>
      )}
      {user ? (
        <>
          <span className="text-emerald-100/70" data-testid="account-email">{user.email}</span>
          <button type="button" className="text-emerald-100/60 hover:text-emerald-100"
                  onClick={() => void logout()} data-testid="logout-button">退出</button>
          <button type="button" className="text-rose-300/70 hover:text-rose-200"
                  onClick={() => void deleteAccount()} data-testid="delete-account">注销</button>
        </>
      ) : (
        <span className="text-emerald-100/50">未登录（本地模式）</span>
      )}

      {historyOpen && (
        <div className="surface-card-muted mt-2 w-full p-3" data-testid="history-panel">
          <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
            <span className="text-[11px] font-semibold uppercase tracking-[0.16em] text-emerald-100/50">历史任务</span>
            <label className="flex items-center gap-1 text-[11px] text-emerald-100/70">
              状态
              <select
                className="rounded border border-white/10 bg-black/30 px-2 py-1 text-[11px]"
                data-testid="history-status-filter"
                value={historyStatus}
                onChange={(event) => {
                  setHistoryStatus(event.target.value)
                  setHistory(null)
                  void loadHistory(true, event.target.value)
                }}
              >
                <option value="">全部</option>
                <option value="SUCCEEDED">已完成</option>
                <option value="FAILED">失败</option>
                <option value="CANCELLED">已取消</option>
                <option value="TIMED_OUT">已超时</option>
                <option value="RUNNING">运行中</option>
              </select>
            </label>
          </div>
          {historyError && <p className="text-amber-200/80" data-testid="history-error">{historyError}</p>}
          {!historyError && history === null && <p className="text-emerald-100/60">加载中…</p>}
          {!historyError && history && history.length === 0 && <p className="text-emerald-100/60">暂无历史任务</p>}
          {!historyError && history && history.length > 0 && (
            <ul className="space-y-2">
              {history.map((item) => (
                <li key={item.run_id} className="flex flex-wrap items-center justify-between gap-2 text-emerald-50/90">
                  <span className="min-w-0 flex-1 truncate">
                    <code className="mr-2 text-[11px] text-emerald-200/70">{item.run_id}</code>
                    {item.topic}
                  </span>
                  <span className="flex flex-wrap items-center gap-2 text-[11px] text-emerald-100/60">
                    {STATUS_LABELS[item.status] ?? item.status}
                    {item.moderation_status && item.moderation_status !== 'cleared' && (
                      <span className="rounded border border-amber-300/40 px-1 text-amber-200"
                            data-testid="flagged-badge">
                        {item.moderation_status === 'under_review' ? '审核中' : '已标记'}
                      </span>
                    )}
                    {item.created_at && <time dateTime={item.created_at}>{formatDateTime(item.created_at)}</time>}
                    {item.has_report && (
                      <button type="button" className="underline hover:text-emerald-100"
                              onClick={() => void openReport(item.run_id)}>查看报告</button>
                    )}
                  </span>
                </li>
              ))}
            </ul>
          )}
          {!historyError && historyHasMore && (
            <button type="button" className="mt-3 text-[11px] text-emerald-200/70 underline hover:text-emerald-100"
                    data-testid="history-load-more" disabled={historyLoadingMore}
                    onClick={() => void loadHistory(false)}>
              {historyLoadingMore ? '加载中…' : '加载更多'}
            </button>
          )}
        </div>
      )}

      {kbOpen && (
        <div className="surface-card-muted mt-2 w-full max-w-sm p-3 sm:ml-auto" data-testid="kb-panel">
          <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
            <span className="text-[11px] font-semibold uppercase tracking-[0.16em] text-emerald-100/50">知识库</span>
            <button type="button" className="text-[11px] text-emerald-200/70 underline hover:text-emerald-100"
                    data-testid="kb-refresh" onClick={() => void refreshSideData()}>
              刷新
            </button>
          </div>

          {uploads.length > 0 && (
            <div className="mb-3" data-testid="upload-queue" aria-live="polite">
              <p className="mb-1 text-[11px] text-emerald-100/50">上传队列</p>
              <ul className="max-h-40 space-y-2 overflow-y-auto pr-1">
                {uploads.map((item) => {
                  const width =
                    item.status === 'uploading' ? item.percent : item.status === 'queued' ? 0 : 100
                  const barClass =
                    item.status === 'error'
                      ? 'bg-gradient-to-r from-rose-500 to-rose-300'
                      : item.status === 'done'
                        ? 'bg-gradient-to-r from-emerald-500 to-emerald-300'
                        : 'bg-gradient-to-r from-emerald-500 via-emerald-300 to-brand-300'
                  return (
                    <li key={item.id} data-testid="upload-item"
                        className="rounded-lg border border-white/[0.06] bg-black/20 px-3 py-2">
                      <div className="flex flex-wrap items-center justify-between gap-2 text-[11px]">
                        <span className="min-w-0 flex-1 truncate text-emerald-50/90" title={item.name}>
                          {item.name}
                        </span>
                        <span className="tabular-nums text-emerald-100/50">{formatBytes(item.size)}</span>
                        <span className={item.status === 'error' ? 'text-rose-300' : 'text-emerald-100/70'}>
                          {uploadLabel(item)}
                        </span>
                      </div>
                      <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-black/25" role="progressbar"
                           data-testid="upload-progress"
                           aria-valuenow={item.status === 'uploading' ? item.percent : undefined}
                           aria-valuemin={0} aria-valuemax={100}>
                        <div
                          className={`relative h-full overflow-hidden rounded-full transition-[width] duration-300 ${barClass}`}
                          style={{ width: `${width}%` }}
                        >
                          {(item.status === 'processing' || (item.status === 'uploading' && item.percent < 100)) && (
                            <span className="pointer-events-none absolute inset-0 overflow-hidden rounded-full">
                              <span className="absolute inset-y-0 left-0 w-1/3 animate-shimmer bg-gradient-to-r from-transparent via-white/45 to-transparent" />
                            </span>
                          )}
                        </div>
                      </div>
                    </li>
                  )
                })}
              </ul>
            </div>
          )}

          <p className="mb-1 text-[11px] text-emerald-100/50">已上传文件</p>
          {docsError && <p role="alert" className="text-amber-200/80" data-testid="kb-error">知识库不可用：{docsError}</p>}
          {!docsError && docs === null && <p className="text-emerald-100/60">加载中…</p>}
          {!docsError && docs && docs.length === 0 && <p className="text-emerald-100/60">还没有上传文档</p>}
          {!docsError && docs && docs.length > 0 && (
            <ul className="max-h-56 space-y-2 overflow-y-auto pr-1">
              {docs.map((doc) => (
                <li key={doc.doc_id || doc.source} data-testid="kb-doc-item"
                    className="flex flex-wrap items-center justify-between gap-2 text-emerald-50/90">
                  <span className="min-w-0 flex-1 truncate" title={doc.source}>{doc.source}</span>
                  <span className="text-[11px] tabular-nums text-emerald-100/60">{doc.chunks} 块</span>
                  {doc.doc_id && (
                    <code className="text-[11px] text-emerald-200/60">{doc.doc_id.split(':').pop()}</code>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {inviteOpen && !authRequired && (
        <Modal
          onClose={() => setInviteOpen(false)}
          labelledBy="invite-title"
          testId="invite-register"
          overlayClassName="z-50 flex items-center justify-center bg-black/70 p-4 backdrop-blur-sm"
          panelClassName="surface-card w-full max-w-md p-6"
        >
          <form onSubmit={(event) => void submitAuth(event)}>
            <div className="flex items-start justify-between gap-3">
              <h2 id="invite-title" className="text-lg font-semibold text-emerald-50">邀请制注册</h2>
              <button type="button" className="text-xs text-emerald-200/70 hover:text-emerald-100"
                      data-testid="invite-close"
                      onClick={() => setInviteOpen(false)}>关闭</button>
            </div>
            <p className="mt-1 text-xs text-emerald-100/60">邀请码已从链接预填；注册成功后自动登录。</p>
            <label className="field-label mt-5" htmlFor="invite-email">邮箱</label>
            <input id="invite-email" name="email" className="field-control" type="email" autoComplete="email"
                   aria-invalid={authError ? true : undefined}
                   aria-describedby={authError ? 'auth-error' : undefined}
                   value={email} onChange={(event) => setEmail(event.target.value)} required />
            <label className="field-label mt-4" htmlFor="invite-password">密码（至少 10 位）</label>
            <input id="invite-password" name="password" className="field-control" type="password"
                   autoComplete="new-password" value={password} minLength={10}
                   aria-invalid={authError ? true : undefined}
                   aria-describedby={authError ? 'auth-error' : undefined}
                   onChange={(event) => setPassword(event.target.value)} required />
            <label className="field-label mt-4" htmlFor="invite-code">邀请码</label>
            <input id="invite-code" name="invite_code" className="field-control" data-testid="invite-code-input"
                   autoComplete="off" spellCheck={false}
                   value={inviteCode} onChange={(event) => setInviteCode(event.target.value)} required />
            {authError && (
              <p id="auth-error" role="alert" className="mt-4 text-sm text-rose-300"
                 data-testid="auth-error">{authError}</p>
            )}
            <button type="submit" className="primary-button mt-6 w-full" disabled={busy}>
              {busy ? '提交中…' : '注册并登录'}
            </button>
            <p className="mt-3 text-center text-[11px] text-emerald-100/50">
              注册即表示同意
              <button type="button" className="mx-1 underline hover:text-emerald-100"
                      onClick={() => void openLegal('terms')}>用户协议</button>
              与
              <button type="button" className="mx-1 underline hover:text-emerald-100"
                      onClick={() => void openLegal('privacy')}>隐私政策</button>
            </p>
          </form>
        </Modal>
      )}
      {legalModal}

      {(preview || previewError) && (
        <Modal
          onClose={() => { setPreview(null); setPreviewError('') }}
          labelledBy="preview-title"
          testId="history-preview"
          overlayClassName="z-50 flex justify-center overflow-y-auto bg-black/70 p-4 backdrop-blur-sm"
          panelClassName="surface-card my-6 w-full max-w-3xl p-6"
        >
          <div className="flex items-center justify-between">
            <h3 id="preview-title" className="text-sm font-semibold text-emerald-50">
              历史报告 {preview?.runId}
            </h3>
            <button type="button" className="text-xs text-emerald-200/70 hover:text-emerald-100"
                    onClick={() => { setPreview(null); setPreviewError('') }}>关闭</button>
          </div>
          {previewError && <p role="alert" className="mt-3 text-sm text-rose-300">{previewError}</p>}
          {preview && (
            <div className="mt-4">
              <ReportView report={preview.markdown} />
            </div>
          )}
        </Modal>
      )}
    </div>
  )
}
