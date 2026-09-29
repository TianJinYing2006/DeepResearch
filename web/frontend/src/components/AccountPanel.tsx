import { useCallback, useEffect, useRef, useState } from 'react'
import HistoryPanel from '../features/history/HistoryPanel'
import { uploadLabel, useUploads } from '../features/knowledge-base/useUploads'
import { csrfHeaders, readErrorMessage } from '../lib/api'
import { formatBytes, formatCny } from '../lib/format'
import type { Quota, RagDoc, SessionUser } from '../types/api'
import Modal from './Modal'
import { ReportView } from './ReportView'

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
 * P6-A 账号面板：登录/注册门、账号条、配额、历史任务（HistoryPanel）、知识库上传。
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
  // 登出/注销后自增：让 HistoryPanel 重挂载清空内部状态（R3/U41）
  const [historyEpoch, setHistoryEpoch] = useState(0)
  const [kbOpen, setKbOpen] = useState(false)
  const [barMessage, setBarMessage] = useState('')
  const [authNotice, setAuthNotice] = useState('')
  const [busy, setBusy] = useState(false)
  const inviteFromUrl = useRef(inviteFromLocation()).current
  const [mode, setMode] = useState<'login' | 'register'>(inviteFromUrl ? 'register' : 'login')
  const [inviteOpen, setInviteOpen] = useState(Boolean(inviteFromUrl))
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [inviteCode, setInviteCode] = useState(inviteFromUrl)
  const [authError, setAuthError] = useState('')
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

  const { uploads, uploadState, addFiles, retryUpload, removeUpload, cancelUpload, clearUploads } =
    useUploads(() => void refreshSideData())

  /** R3（审计 U41）：登出/注销后清空本人可见的本地状态，避免上一账号数据闪现。 */
  const resetLocalData = useCallback(() => {
    setQuota(null)
    setDocs(null)
    setDocsError('')
    setBarMessage('')
    setHistoryOpen(false)
    setHistoryEpoch((epoch) => epoch + 1)
    clearUploads()
  }, [clearUploads])

  useEffect(() => {
    void loadSession()
  }, [loadSession])

  // R3（审计 U42）：邀请参数一次性消费 —— 之后刷新/前进后退不再重开注册弹窗
  useEffect(() => {
    if (!inviteFromUrl) return
    const url = new URL(window.location.href)
    url.searchParams.delete('invite')
    window.history.replaceState({}, '', `${url.pathname}${url.search}${url.hash}`)
  }, [inviteFromUrl])

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
      setAuthNotice('')
      void refreshSideData()
    } catch {
      setAuthError('网络错误，请重试')
    } finally {
      setBusy(false)
    }
  }

  async function logout() {
    try {
      const response = await fetch('/api/auth/logout', { method: 'POST' })
      if (!response.ok) {
        setBarMessage('退出失败，请重试')
        return
      }
    } catch {
      setBarMessage('网络错误，请重试')
      return
    }
    setUser(null)
    resetLocalData()
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
        setBarMessage(await readErrorMessage(response))
        return
      }
      const body = (await response.json()) as { rag_cleanup: string }
      setUser(null)
      resetLocalData()
      setAuthNotice(`账号已注销（知识库清理：${body.rag_cleanup}）`)
    } catch {
      setBarMessage('网络错误，请重试')
    }
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
      overlayClassName="z-[60] flex justify-center overflow-y-auto bg-ink/35 p-4 "
      panelClassName="surface-card my-6 w-full max-w-3xl p-6"
    >
      <div className="flex items-center justify-between">
        <h3 id="legal-title" className="text-sm font-semibold text-ink">
          {legal.doc === 'privacy' ? '隐私政策' : '用户协议'}
        </h3>
        <button type="button" className="text-xs text-ink-muted hover:text-ink"
                data-testid="legal-close"
                onClick={() => { setLegal(null); setLegalError('') }}>关闭</button>
      </div>
      {legalError && <p role="alert" className="mt-3 text-sm text-stamp-red">{legalError}</p>}
      {!legalError && !legal.markdown && <p className="mt-3 text-sm text-ink-muted">加载中…</p>}
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
        overlayClassName="z-50 flex items-center justify-center bg-ink/35 p-4 "
        panelClassName="surface-card w-full max-w-md p-6"
      >
        <form onSubmit={(event) => void submitAuth(event)}>
          <h2 id="auth-title" className="text-lg font-semibold text-ink">
            {mode === 'login' ? '登录 DeepResearch' : '邀请制注册'}
          </h2>
          <p className="mt-1 text-xs text-ink-muted">
            {mode === 'login' ? '使用邮箱与密码登录' : '需要一次性邀请码（管理员通过 CLI 生成）'}
          </p>
          {authNotice && (
            <p role="status" className="mt-3 rounded-lg border border-stamp-green/30 bg-stamp-green/10 px-3 py-2 text-xs text-ink"
               data-testid="auth-notice">{authNotice}</p>
          )}
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
            <p id="auth-error" role="alert" className="mt-4 text-sm text-stamp-red"
               data-testid="auth-error">{authError}</p>
          )}
          <button type="submit" className="primary-button mt-6 w-full" disabled={busy}>
            {busy ? '提交中…' : mode === 'login' ? '登录' : '注册并登录'}
          </button>
          <button type="button" className="mt-3 w-full text-xs text-ink-muted hover:text-ink"
                  onClick={() => { setMode(mode === 'login' ? 'register' : 'login'); setAuthError('') }}>
            {mode === 'login' ? '有邀请码？去注册' : '已有账号？去登录'}
          </button>
          <p className="mt-3 text-center text-[11px] text-ink-muted">
            注册即表示同意
            <button type="button" className="mx-1 underline hover:text-ink"
                    onClick={() => void openLegal('terms')}>用户协议</button>
            与
            <button type="button" className="mx-1 underline hover:text-ink"
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
        <span className="rounded-full border border-rule bg-rule/30 px-3 py-1.5 tabular-nums text-ink-muted"
              data-testid="quota-chip">
          {quotaLine}
        </span>
      )}
      <button type="button" className="rounded-full border border-rule px-3 py-1.5 text-ink hover:border-stamp-blue/50"
              onClick={() => setHistoryOpen((open) => !open)} data-testid="history-toggle">
        历史任务
      </button>
      <label className="cursor-pointer rounded-full border border-rule px-3 py-1.5 text-ink hover:border-stamp-blue/50">
        上传文档
        <input type="file" accept=".pdf,.docx,.md,.markdown,.txt" multiple className="hidden"
               data-testid="rag-upload-input"
               onChange={(event) => {
                 if (event.target.files?.length) {
                   setKbOpen(true)
                   void addFiles(event.target.files)
                 }
                 event.target.value = ''
               }} />
      </label>
      <button type="button" className="rounded-full border border-rule px-3 py-1.5 text-ink hover:border-stamp-blue/50"
              onClick={() => { setKbOpen((open) => !open); void refreshSideData() }} data-testid="kb-toggle">
        知识库 {docs ? `${docs.length} 篇` : docsError ? '不可用' : '…'}
      </button>
      {uploadState && (
        <span className="text-ink-muted" role="status" aria-live="polite"
              data-testid="upload-state">{uploadState}</span>
      )}
      {barMessage && (
        <span className="text-stamp-amber" role="status" aria-live="polite">{barMessage}</span>
      )}
      {user ? (
        <>
          <span className="text-ink-muted" data-testid="account-email">{user.email}</span>
          <button type="button" className="text-ink-muted hover:text-ink"
                  onClick={() => void logout()} data-testid="logout-button">退出</button>
          <button type="button" className="text-stamp-red/80 hover:text-stamp-red"
                  onClick={() => void deleteAccount()} data-testid="delete-account">注销</button>
        </>
      ) : (
        <span className="text-ink-muted">未登录（本地模式）</span>
      )}

      <HistoryPanel key={historyEpoch} open={historyOpen} />

      {kbOpen && (
        <div className="surface-card-muted mt-2 w-full max-w-sm p-3 sm:ml-auto" data-testid="kb-panel">
          <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
            <h3 className="text-[11px] font-medium text-ink-muted">知识库</h3>
            <button type="button" className="text-[11px] text-ink-muted underline hover:text-ink"
                    data-testid="kb-refresh" onClick={() => void refreshSideData()}>
              刷新
            </button>
          </div>

          {uploads.length > 0 && (
            <div className="mb-3" data-testid="upload-queue" aria-live="polite">
              <p className="mb-1 text-[11px] text-ink-muted">上传队列</p>
              <ul className="max-h-40 space-y-2 overflow-y-auto pr-1">
                {uploads.map((item) => {
                  const width =
                    item.status === 'uploading'
                      ? item.percent
                      : item.status === 'queued' || item.status === 'cancelled'
                        ? 0
                        : 100
                  const barClass =
                    item.status === 'error'
                      ? 'bg-stamp-red'
                      : item.status === 'cancelled'
                        ? 'bg-ink-muted/30'
                        : item.status === 'done'
                          ? 'bg-stamp-green'
                          : 'bg-stamp-blue'
                  return (
                    <li key={item.id} data-testid="upload-item"
                        className="rounded-lg border border-rule bg-rule/40 px-3 py-2">
                      <div className="flex flex-wrap items-center justify-between gap-2 text-[11px]">
                        <span className="min-w-0 flex-1 truncate text-ink" title={item.name}>
                          {item.name}
                        </span>
                        <span className="tabular-nums text-ink-muted">{formatBytes(item.size)}</span>
                        <span className={item.status === 'error' ? 'text-stamp-red' : 'text-ink-muted'}>
                          {uploadLabel(item)}
                        </span>
                      </div>
                      <div className="relative mt-2 h-1.5 overflow-hidden rounded-full bg-rule/40" role="progressbar"
                           data-testid="upload-progress" aria-label={`${item.name} 上传进度`}
                           aria-valuenow={item.status === 'uploading' ? item.percent : undefined}
                           aria-valuemin={0} aria-valuemax={100}>
                        <div
                          className={`h-full w-full origin-left rounded-full transition-transform duration-300 ${barClass}`}
                          style={{ transform: `scaleX(${width / 100})` }}
                        />
                        {(item.status === 'processing' || (item.status === 'uploading' && item.percent < 100)) && (
                          <span className="pointer-events-none absolute inset-0 overflow-hidden rounded-full">
                            <span className="absolute inset-y-0 left-0 w-1/3 animate-shimmer bg-gradient-to-r from-transparent via-white/45 to-transparent" />
                          </span>
                        )}
                      </div>
                      <div className="mt-2 flex items-center justify-end gap-3 text-[11px]">
                        {(item.status === 'error' || item.status === 'cancelled') && (
                          <button type="button" className="underline text-stamp-blue hover:text-ink"
                                  data-testid="upload-retry" onClick={() => retryUpload(item)}>重试</button>
                        )}
                        {(item.status === 'queued' || item.status === 'uploading' || item.status === 'processing') && (
                          <button type="button" className="text-stamp-amber hover:text-stamp-amber"
                                  data-testid="upload-cancel" onClick={() => cancelUpload(item.id)}>取消</button>
                        )}
                        <button type="button" className="text-ink-muted hover:text-ink"
                                data-testid="upload-remove" onClick={() => removeUpload(item.id)}>移除</button>
                      </div>
                    </li>
                  )
                })}
              </ul>
            </div>
          )}

          <p className="mb-1 text-[11px] text-ink-muted">已上传文件</p>
          {docsError && <p role="alert" className="text-stamp-amber" data-testid="kb-error">知识库不可用：{docsError}</p>}
          {!docsError && docs === null && <p className="text-ink-muted">加载中…</p>}
          {!docsError && docs && docs.length === 0 && <p className="text-ink-muted">还没有上传文档</p>}
          {!docsError && docs && docs.length > 0 && (
            <ul className="max-h-56 space-y-2 overflow-y-auto pr-1"
                tabIndex={0} role="region" aria-label="已上传文件列表">
              {docs.map((doc) => (
                <li key={doc.doc_id || doc.source} data-testid="kb-doc-item"
                    className="flex flex-wrap items-center justify-between gap-2 text-ink">
                  <span className="min-w-0 flex-1 truncate" title={doc.source}>{doc.source}</span>
                  <span className="text-[11px] tabular-nums text-ink-muted">{doc.chunks} 块</span>
                  {doc.doc_id && (
                    <code className="text-[11px] text-ink-muted">{doc.doc_id.split(':').pop()}</code>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {inviteOpen && !authRequired && !user && (
        <Modal
          onClose={() => setInviteOpen(false)}
          labelledBy="invite-title"
          testId="invite-register"
          overlayClassName="z-50 flex items-center justify-center bg-ink/35 p-4 "
          panelClassName="surface-card w-full max-w-md p-6"
        >
          <form onSubmit={(event) => void submitAuth(event)}>
            <div className="flex items-start justify-between gap-3">
              <h2 id="invite-title" className="text-lg font-semibold text-ink">邀请制注册</h2>
              <button type="button" className="text-xs text-ink-muted hover:text-ink"
                      data-testid="invite-close"
                      onClick={() => setInviteOpen(false)}>关闭</button>
            </div>
            <p className="mt-1 text-xs text-ink-muted">邀请码已从链接预填；注册成功后自动登录。</p>
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
              <p id="auth-error" role="alert" className="mt-4 text-sm text-stamp-red"
                 data-testid="auth-error">{authError}</p>
            )}
            <button type="submit" className="primary-button mt-6 w-full" disabled={busy}>
              {busy ? '提交中…' : '注册并登录'}
            </button>
            <p className="mt-3 text-center text-[11px] text-ink-muted">
              注册即表示同意
              <button type="button" className="mx-1 underline hover:text-ink"
                      onClick={() => void openLegal('terms')}>用户协议</button>
              与
              <button type="button" className="mx-1 underline hover:text-ink"
                      onClick={() => void openLegal('privacy')}>隐私政策</button>
            </p>
          </form>
        </Modal>
      )}
      {legalModal}
    </div>
  )
}
