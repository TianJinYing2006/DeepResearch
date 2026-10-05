import { useCallback, useEffect, useRef, useState } from 'react'
import AuthForm from '../features/auth/AuthForm'
import AuthGate from '../features/auth/AuthGate'
import LandingPage from '../features/landing/LandingPage'
import HistoryPanel from '../features/history/HistoryPanel'
import { uploadLabel, useUploads } from '../features/knowledge-base/useUploads'
import { csrfHeaders, readErrorMessage } from '../lib/api'
import { formatBytes, formatCny } from '../lib/format'
import type { Quota, RagChunk, RagDoc, SessionUser } from '../types/api'
import FeedbackModal from './FeedbackModal'
import HelpModal from './HelpModal'
import Modal from './Modal'
import { ReportView } from './ReportView'
import SecurityPanel from './SecurityPanel'
import { SkeletonRows } from './ui'

/** 需求 23：locator → 可读定位（页码 / 幻灯片 / 工作表 / 行范围）。 */
function locatorLabel(locator: Record<string, unknown>): string {
  const parts: string[] = []
  if (typeof locator.page === 'number') parts.push(`第 ${locator.page} 页`)
  if (typeof locator.slide === 'number') parts.push(`第 ${locator.slide} 页幻灯片`)
  if (typeof locator.sheet === 'string') parts.push(`工作表 ${locator.sheet}`)
  const range = locator.row_range
  if (Array.isArray(range) && range.length === 2) parts.push(`行 ${range[0]}-${range[1]}`)
  return parts.join(' · ') || '全文'
}

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
  /** 需求 26：报告分享开关（透传给历史面板，控制分享入口显示）。 */
  shareEnabled?: boolean
}

/**
 * P6-A 账号面板：登录/注册门、账号条、配额、历史任务（HistoryPanel）、知识库上传。
 *
 * - 鉴权开启（authRequired）且未登录时渲染全屏登录门；
 * - 未开启鉴权时保持匿名可用（历史/上传仍可用，配额由后端按匿名口径返回）。
 */
export default function AccountPanel({ authRequired, activeRunId, running, shareEnabled = false }: Props) {
  const [user, setUser] = useState<SessionUser | null>(null)
  const [checked, setChecked] = useState(false)
  const [quota, setQuota] = useState<Quota | null>(null)
  const [docs, setDocs] = useState<RagDoc[] | null>(null)
  const [docsError, setDocsError] = useState('')
  const [historyOpen, setHistoryOpen] = useState(false)
  // 登出/注销后自增：让 HistoryPanel 重挂载清空内部状态（R3/U41）
  const [historyEpoch, setHistoryEpoch] = useState(0)
  const [kbOpen, setKbOpen] = useState(false)
  const [securityOpen, setSecurityOpen] = useState(false)
  const [barMessage, setBarMessage] = useState('')
  // R7：KB 文档删除（两步内联确认 —— 不用 window.confirm 反模式）
  const [deleteDocId, setDeleteDocId] = useState<string | null>(null)
  const [deletingDoc, setDeletingDoc] = useState(false)
  // 需求 23：分块预览 / 重命名 / 重分块 / 重嵌入 / 容量
  const [previewDoc, setPreviewDoc] = useState<{ docId: string; source: string } | null>(null)
  const [previewChunks, setPreviewChunks] = useState<RagChunk[] | null>(null)
  const [previewTotal, setPreviewTotal] = useState(0)
  const [previewError, setPreviewError] = useState('')
  const [renamingDocId, setRenamingDocId] = useState<string | null>(null)
  const [renameValue, setRenameValue] = useState('')
  const [kbBusyDocId, setKbBusyDocId] = useState<string | null>(null)
  const [kbActionError, setKbActionError] = useState('')
  const [kbUsage, setKbUsage] = useState<{ used_bytes: number; quota_bytes: number | null } | null>(null)
  const [authNotice, setAuthNotice] = useState('')
  // 需求 25：帮助中心 / 站内反馈
  const [helpOpen, setHelpOpen] = useState(false)
  const [feedbackOpen, setFeedbackOpen] = useState(false)
  const inviteFromUrl = useRef(inviteFromLocation()).current
  const [inviteOpen, setInviteOpen] = useState(Boolean(inviteFromUrl))
  // 未登录访客的分流视图：落地页（默认）→ 登录注册页；邀请链接 / ?login / 重置链接直达登录页
  const [authView, setAuthView] = useState<'landing' | 'auth'>(() => {
    if (inviteFromUrl) return 'auth'
    try {
      if (window.location.hash.startsWith('#reset=')) return 'auth'
      return new URLSearchParams(window.location.search).has('login') ? 'auth' : 'landing'
    } catch {
      return 'landing'
    }
  })
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
    const [quotaResp, docsResp, usageResp] = await Promise.all([
      fetch('/api/quota'), fetch('/api/rag/docs'), fetch('/api/rag/usage'),
    ])
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
    if (usageResp.ok) {
      setKbUsage((await usageResp.json()) as { used_bytes: number; quota_bytes: number | null })
    } else {
      setKbUsage(null)
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
    setSecurityOpen(false)
    setAuthView(inviteFromUrl ? 'auth' : 'landing')
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

  /** R7：删除知识库文档 —— 后端同步删向量并验证归零（失败 503，可稍后重试）。 */
  async function deleteDoc(docId: string) {
    setDeletingDoc(true)
    try {
      const response = await fetch(`/api/rag/docs?doc_id=${encodeURIComponent(docId)}`, {
        method: 'DELETE',
        headers: csrfHeaders(),
      })
      if (!response.ok) {
        setBarMessage(await readErrorMessage(response))
        return
      }
      setDeleteDocId(null)
      await refreshSideData()
    } catch {
      setBarMessage('网络错误，请重试')
    } finally {
      setDeletingDoc(false)
    }
  }

  /** 需求 23：知识库写操作（重命名 PATCH / 重分块 / 重嵌入）。 */
  async function kbMutate(docId: string, suffix: string, body?: unknown, method = 'POST') {
    if (kbBusyDocId !== null) return
    setKbActionError('')
    setKbBusyDocId(docId)
    try {
      const response = await fetch(`/api/rag/docs${suffix}`, {
        method,
        headers: { 'Content-Type': 'application/json', ...csrfHeaders() },
        body: body === undefined ? undefined : JSON.stringify(body),
      })
      if (!response.ok) {
        setKbActionError(await readErrorMessage(response))
        return
      }
      setRenamingDocId(null)
      await refreshSideData()
    } catch {
      setKbActionError('网络错误，请重试')
    } finally {
      setKbBusyDocId(null)
    }
  }

  /** 需求 23：分块预览（默认活动版本；locator 供引用回溯定位）。 */
  async function openPreview(docId: string, source: string) {
    setPreviewDoc({ docId, source })
    setPreviewChunks(null)
    setPreviewTotal(0)
    setPreviewError('')
    try {
      const response = await fetch(`/api/rag/docs/${encodeURIComponent(docId)}/chunks?limit=50`)
      if (!response.ok) {
        setPreviewError(await readErrorMessage(response))
        return
      }
      const body = (await response.json()) as { total: number; chunks: RagChunk[] }
      setPreviewChunks(body.chunks)
      setPreviewTotal(body.total)
    } catch {
      setPreviewError('网络错误，请重试')
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
      overlayClassName="z-50 flex items-start justify-center overflow-y-auto bg-ink/35 p-4 "
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
    // 分流：未登录访客先看落地页；CTA / 邀请链接 / ?login 进入登录注册页
    if (authView === 'landing') {
      return (
        <>
          <LandingPage
            onStart={() => setAuthView('auth')}
            onOpenLegal={(doc) => void openLegal(doc)}
            onOpenHelp={() => setHelpOpen(true)}
          />
          {legalModal}
          {helpOpen && <HelpModal onClose={() => setHelpOpen(false)} />}
        </>
      )
    }
    return (
      <>
        <AuthGate
          notice={authNotice}
          inviteFromUrl={inviteFromUrl}
          onBack={() => setAuthView('landing')}
          onAuthed={(next) => {
            setUser(next)
            setAuthNotice('')
            void refreshSideData()
          }}
          onOpenLegal={(doc) => void openLegal(doc)}
        />
        {legalModal}
      </>
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
      {/* R7（审计 U1）：输入框改 sr-only —— 视觉隐藏但**可 Tab 聚焦、可键盘激活**；
          焦点环画在 label 上（focus-within），鼠标点击路径不变。 */}
      <label className="cursor-pointer rounded-full border border-rule px-3 py-1.5 text-ink hover:border-stamp-blue/50 focus-within:ring-2 focus-within:ring-stamp-blue/40"
             title="支持 PDF / Word / PPT / Excel / Markdown / 文本 / HTML">
        上传文档
        <input type="file" multiple className="sr-only"
               accept=".pdf,.docx,.pptx,.xlsx,.md,.markdown,.txt,.text,.html,.htm"
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
      {user && (
        <button type="button" className="rounded-full border border-rule px-3 py-1.5 text-ink hover:border-stamp-blue/50"
                onClick={() => setSecurityOpen((open) => !open)} data-testid="security-toggle">
          会话与安全
        </button>
      )}
      {/* 需求 25：帮助中心（所有人可达）；反馈（登录用户；本地未开鉴权时也可用） */}
      <button type="button" className="rounded-full border border-rule px-3 py-1.5 text-ink hover:border-stamp-blue/50"
              onClick={() => setHelpOpen(true)} data-testid="help-toggle">
        帮助
      </button>
      {(user || !authRequired) && (
        <button type="button" className="rounded-full border border-rule px-3 py-1.5 text-ink hover:border-stamp-blue/50"
                onClick={() => setFeedbackOpen(true)} data-testid="feedback-toggle">
          反馈
        </button>
      )}
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

      <HistoryPanel key={historyEpoch} open={historyOpen} shareEnabled={shareEnabled} />

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
                  // 进度语义：上传中/失败 = 实际字节进度（失败保留断点）；完成 = 满格；
                  // 排队/处理中/取消 = 0（处理中走「不确定态滑光」，不假装知道百分比）
                  const width =
                    item.status === 'uploading' || item.status === 'error'
                      ? item.percent
                      : item.status === 'done'
                        ? 100
                        : 0
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
                           aria-valuenow={item.status === 'uploading' || item.status === 'error' ? item.percent : undefined}
                           aria-valuetext={uploadLabel(item)}
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
          {kbUsage && (
            <p className="mb-1 text-[11px] tabular-nums text-ink-muted" data-testid="kb-usage">
              已用 {formatBytes(kbUsage.used_bytes)}
              {kbUsage.quota_bytes ? ` / ${formatBytes(kbUsage.quota_bytes)}` : ''}
            </p>
          )}
          {kbActionError && (
            <p role="alert" className="mb-1 text-[11px] text-stamp-amber"
               data-testid="kb-action-error">{kbActionError}</p>
          )}
          {docsError && <p role="alert" className="text-stamp-amber" data-testid="kb-error">知识库不可用：{docsError}</p>}
          {!docsError && docs === null && (
            <div role="status" aria-live="polite">
              <span className="sr-only">加载中…</span>
              <SkeletonRows rows={3} />
            </div>
          )}
          {!docsError && docs && docs.length === 0 && <p className="text-ink-muted">还没有上传文档</p>}
          {!docsError && docs && docs.length > 0 && (
            <ul className="max-h-56 space-y-2 overflow-y-auto pr-1"
                tabIndex={0} role="region" aria-label="已上传文件列表">
              {docs.map((doc) => {
                const status = doc.status ?? 'ready'
                const statusLabel = status === 'ready' ? '就绪' : status === 'rejected' ? '失败' : '处理中'
                const docId = doc.doc_id ?? ''
                return (
                  <li key={docId || doc.source} data-testid="kb-doc-item"
                      className="flex flex-wrap items-center justify-between gap-2 text-ink">
                    <span className="min-w-0 flex-1 truncate" title={doc.source}>
                      {doc.display_name || doc.source}
                    </span>
                    <span className="flex items-center gap-2 text-[11px] text-ink-muted">
                      <span
                        className={status === 'rejected' ? 'text-stamp-red'
                          : status === 'ready' ? 'text-ink-muted' : 'text-stamp-amber'}
                        data-testid="kb-doc-status" title={doc.error ?? undefined}
                      >
                        {statusLabel}
                      </span>
                      {typeof doc.size_bytes === 'number' && (
                        <span className="tabular-nums" data-testid="kb-doc-size">
                          {formatBytes(doc.size_bytes)}
                        </span>
                      )}
                      <span className="tabular-nums">{doc.chunks} 块</span>
                    </span>
                    <span className="flex flex-wrap items-center gap-2 text-[11px]">
                      {docId && status === 'ready' && (
                        <>
                          <button type="button" className="underline text-ink-muted hover:text-ink"
                                  data-testid="kb-doc-preview"
                                  onClick={() => void openPreview(docId, doc.source)}>预览</button>
                          {renamingDocId === docId ? (
                            <>
                              <input
                                className="w-32 rounded border border-rule bg-rule/40 px-1.5 py-0.5 text-ink"
                                data-testid="kb-rename-input"
                                autoFocus value={renameValue}
                                onChange={(event) => setRenameValue(event.target.value)}
                                onKeyDown={(event) => {
                                  if (event.key === 'Enter') {
                                    void kbMutate(docId, '', { doc_id: docId, display_name: renameValue }, 'PATCH')
                                  }
                                  if (event.key === 'Escape') setRenamingDocId(null)
                                }}
                              />
                              <button type="button" className="underline hover:text-ink"
                                      disabled={kbBusyDocId !== null || !renameValue.trim()}
                                      onClick={() => void kbMutate(docId, '', { doc_id: docId, display_name: renameValue }, 'PATCH')}>
                                保存
                              </button>
                            </>
                          ) : (
                            <button type="button" className="underline text-ink-muted hover:text-ink"
                                    data-testid="kb-rename" disabled={kbBusyDocId !== null}
                                    onClick={() => {
                                      setRenamingDocId(docId)
                                      setRenameValue(doc.display_name || doc.source)
                                    }}>
                              重命名
                            </button>
                          )}
                          <button type="button" className="underline text-ink-muted hover:text-ink"
                                  data-testid="kb-rechunk" disabled={kbBusyDocId !== null}
                                  title="从解析快照重新分块（换分块器）"
                                  onClick={() => void kbMutate(docId, '/rechunk', { doc_id: docId })}>
                            重分块
                          </button>
                          <button type="button" className="underline text-ink-muted hover:text-ink"
                                  data-testid="kb-reembed" disabled={kbBusyDocId !== null}
                                  title="同分块重新嵌入（换向量模型 / 修复索引）"
                                  onClick={() => void kbMutate(docId, '/reembed', { doc_id: docId })}>
                            重嵌入
                          </button>
                        </>
                      )}
                      {docId && (
                        deleteDocId === docId ? (
                          <span className="flex items-center gap-2 text-[11px]">
                            <button type="button" className="text-stamp-red hover:text-ink disabled:opacity-60"
                                    data-testid="kb-delete-confirm" disabled={deletingDoc}
                                    onClick={() => void deleteDoc(docId)}>
                              {deletingDoc ? '删除中…' : '确认删除'}
                            </button>
                            <button type="button" className="text-ink-muted hover:text-ink"
                                    data-testid="kb-delete-cancel" disabled={deletingDoc}
                                    onClick={() => setDeleteDocId(null)}>取消</button>
                          </span>
                        ) : (
                          <button type="button" className="text-ink-muted hover:text-stamp-red"
                                  data-testid="kb-delete"
                                  onClick={() => setDeleteDocId(docId)}>删除</button>
                        )
                      )}
                    </span>
                    {status === 'rejected' && doc.error && (
                      <span className="w-full text-[11px] text-stamp-red" data-testid="kb-doc-error">
                        {doc.error}
                      </span>
                    )}
                  </li>
                )
              })}
            </ul>
          )}
        </div>
      )}

      {previewDoc && (
        <Modal
          onClose={() => { setPreviewDoc(null); setPreviewChunks(null); setPreviewError('') }}
          labelledBy="kb-preview-title"
          testId="kb-preview"
          overlayClassName="z-50 flex items-start justify-center overflow-y-auto bg-ink/35 p-4"
          panelClassName="surface-card my-6 w-full max-w-2xl p-6"
        >
          <div className="flex items-center justify-between">
            <h3 id="kb-preview-title" className="text-sm font-semibold text-ink">
              分块预览：{previewDoc.source}（{previewTotal} 块）
            </h3>
            <button type="button" className="text-xs text-ink-muted hover:text-ink"
                    onClick={() => { setPreviewDoc(null); setPreviewChunks(null); setPreviewError('') }}>
              关闭
            </button>
          </div>
          {previewError && <p role="alert" className="mt-3 text-sm text-stamp-red">{previewError}</p>}
          {previewChunks === null && !previewError && (
            <div className="mt-3"><SkeletonRows rows={4} /></div>
          )}
          {previewChunks && (
            <ul className="mt-3 max-h-[60vh] space-y-2 overflow-y-auto" data-testid="kb-chunk-list">
              {previewChunks.map((chunk) => (
                <li key={chunk.chunk_id} className="rounded-lg border border-rule bg-rule/30 px-3 py-2">
                  <p className="text-[11px] text-ink-muted">
                    #{chunk.chunk_index + 1} · {locatorLabel(chunk.locator)}
                  </p>
                  <p className="mt-1 whitespace-pre-wrap text-[13px] leading-relaxed text-ink">
                    {chunk.text.length > 400 ? `${chunk.text.slice(0, 400)}…` : chunk.text}
                  </p>
                </li>
              ))}
            </ul>
          )}
        </Modal>
      )}

      {securityOpen && user && (
        <SecurityPanel onSignedOut={() => { setUser(null); resetLocalData(); setAuthNotice('当前设备已退出登录') }} />
      )}

      {inviteOpen && !authRequired && !user && (
        <Modal
          onClose={() => setInviteOpen(false)}
          labelledBy="invite-title"
          testId="invite-register"
          overlayClassName="z-50 flex items-center justify-center bg-ink/35 p-4 "
          panelClassName="surface-card relative w-full max-w-md p-6"
        >
          <button type="button" className="absolute right-5 top-5 text-xs text-ink-muted transition hover:text-ink"
                  data-testid="invite-close"
                  onClick={() => setInviteOpen(false)}>关闭</button>
          <AuthForm
            idPrefix="invite"
            titleId="invite-title"
            initialMode="register"
            inviteFromUrl={inviteFromUrl}
            subtitle="邀请码已从链接预填；注册成功后自动登录。"
            onAuthed={(next) => {
              setUser(next)
              setInviteOpen(false)
              void refreshSideData()
            }}
            onOpenLegal={(doc) => void openLegal(doc)}
          />
        </Modal>
      )}
      {legalModal}
      {helpOpen && <HelpModal onClose={() => setHelpOpen(false)} />}
      {feedbackOpen && <FeedbackModal onClose={() => setFeedbackOpen(false)} />}
    </div>
  )
}
