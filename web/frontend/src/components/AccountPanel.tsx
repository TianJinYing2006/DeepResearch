import { useCallback, useEffect, useRef, useState } from 'react'
import AuthForm from '../features/auth/AuthForm'
import AuthGate from '../features/auth/AuthGate'
import LandingPage from '../features/landing/LandingPage'
import HistoryPanel from '../features/history/HistoryPanel'
import { uploadLabel, useUploads } from '../features/knowledge-base/useUploads'
import { ChunkPreviewModal, useChunkPreview } from '../features/knowledge-base/ChunkPreviewModal'
import { useRagDocActions } from '../features/knowledge-base/useRagDocActions'
import { useAccountBarUi } from '../features/account/useAccountBarUi'
import { SupportModals, useSupportModals } from '../features/support/SupportModals'
import { LegalModal, useLegalDoc } from '../features/account/LegalModal'
import { csrfHeaders, readErrorMessage } from '../lib/api'
import { useSideData } from '../hooks/useSideData'
import { formatBytes, formatCny } from '../lib/format'
import type { SessionUser } from '../types/api'
import HelpModal from './HelpModal'
import Modal from './Modal'
import SecurityPanel from './SecurityPanel'
import { SkeletonRows } from './ui'

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

  // 验收点③ 切片 S2（state）：账号条 UI 状态已抽到 `features/account/useAccountBarUi.ts`。
  // 解构沿用原名（含 setter），JSX 与全部调用点零改动。
  // ⚠️ `historyEpoch` 与 `barMessage` 的归属是本切片刻意决定的，理由见该文件头部（R4/R6）。
  const {
    barMessage, setBarMessage,
    historyOpen, setHistoryOpen,
    historyEpoch, setHistoryEpoch,
    kbOpen, setKbOpen,
    securityOpen, setSecurityOpen,
    clear: clearBarUi,
  } = useAccountBarUi()
  // 登出/注销后自增：让 HistoryPanel 重挂载清空内部状态（R3/U41）
  const [authNotice, setAuthNotice] = useState('')
  // 需求 25：帮助中心 / 站内反馈
  // 验收点③ 切片 S10：帮助 / 反馈 / 分享管理已抽到 `features/support/SupportModals.tsx`。
  // 解构沿用原名（含 setter），下方全部调用点零改动。
  // ⚠️ 本文件有**两处** HelpModal 挂载：落地页分支单独用 helpOpen/setHelpOpen（保留原名即可），
  // 主分支那三行由 <SupportModals> 负责。
  const support = useSupportModals()
  const {
    helpOpen, setHelpOpen, setFeedbackOpen, setShareManageOpen,
  } = support
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

  // 验收点③ 切片 S4+S5（state）：配额 / 知识库文档 / 知识库容量已抽到 `hooks/useSideData.ts`。
  // 解构时**刻意沿用原名**（含 `refreshSideData`），让下方 JSX 与全部 9 个调用点零改动。
  // ⚠️ 必须声明在 `resetLocalData` 与 `useUploads` **之前** —— 两者都要用到它，而 `const` 没有提升。
  const {
    quota,
    docs,
    docsError,
    kbUsage,
    refresh: refreshSideData,
    clear: clearSideData,
  } = useSideData()

  // 验收点③ 切片 S5（actions）：文档行的删除 / 重命名 / 重分块 / 重嵌入已抽到
  // `features/knowledge-base/useRagDocActions.ts`。同样沿用原名（含 setter），调用方零改动。
  // ⚠️ 跨域依赖显式传入：刷新复用上面的 `refreshSideData`；删除失败走**账号条**错误通道
  // （既有行为，非笔误），故用 `setBarMessage` 经 `onError` 注入。
  const {
    deleteDocId, setDeleteDocId, deletingDoc,
    renamingDocId, setRenamingDocId, renameValue, setRenameValue,
    kbBusyDocId, kbActionError,
    deleteDoc, kbMutate,
    clear: clearRagActions,
  } = useRagDocActions({ refresh: refreshSideData, onError: setBarMessage })

  const { uploads, uploadState, addFiles, retryUpload, removeUpload, cancelUpload, clearUploads } =
    useUploads(() => void refreshSideData())

  // 验收点③ 切片 S6：分块预览已抽到 `features/knowledge-base/ChunkPreviewModal.tsx`。
  // ⚠️ 必须声明在 `resetLocalData` **之前** —— 后者要调 `preview.reset()`，
  // 而 `const` 没有提升，放到后面会直接触发 TDZ 报错。
  const preview = useChunkPreview()

  /** R3（审计 U41）：登出/注销后清空本人可见的本地状态，避免上一账号数据闪现。
   *
   * ⚠️ 这份清单必须是**全量**的。R1~R7 收口时 U41 只清了「配额 + 文档清单」，
   * 知识库一侧漏掉：`kbUsage`（面板里的「已用 X MB」，渲染点 L512–515）与分块预览
   * `previewDoc`（弹窗会连正文一起重新出现）都不清 ⇒ 登出后换个账号登录，
   * 上一账号的知识库用量与预览正文会再次显示，与该函数自己的注释正好相反。
   *
   * 纪律：本文件的每个领域切片若持有**账号级**状态，都必须在此登记一行；
   * 后续拆分 AccountPanel（验收点③）时，这份清单是切片间唯一的共同写操作，不得遗漏。 */
  const resetLocalData = useCallback(() => {
    clearSideData()
    clearBarUi()
    setAuthView(inviteFromUrl ? 'auth' : 'landing')
    setHistoryEpoch((epoch) => epoch + 1)
    clearUploads()
    // —— 知识库侧（U41 补全）——
    clearRagActions()
    // S10：三扇支撑弹窗也是账号级状态（分享管理列的是本人分享），一并关掉
    support.clear()
    // 预览弹窗：**必须**连状态一起清 —— 它受 `previewDoc` 驱动而非 `user` 驱动，
    // 在「鉴权可选」的部署里登出不会卸载该分支，弹窗会带着上一账号的分块正文留在屏幕上。
    preview.reset()
  }, [clearSideData, clearUploads, clearRagActions, inviteFromUrl, preview.reset, support])

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

  // 验收点③ 切片 S7：法律文本域已抽到 `features/account/LegalModal.tsx`。
  // 三个挂载点（落地页 / 登录门 / 账号条）仍保持互斥，理由见该文件顶部的拆分纪律。
  const legalDoc = useLegalDoc()

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

  const legalModal = (
    <LegalModal legal={legalDoc.legal} legalError={legalDoc.legalError} onClose={legalDoc.close} />
  )

  if (authRequired && checked && !user) {
    // 分流：未登录访客先看落地页；CTA / 邀请链接 / ?login 进入登录注册页
    if (authView === 'landing') {
      return (
        <>
          <LandingPage
            onStart={() => setAuthView('auth')}
            onOpenLegal={(doc) => void legalDoc.open(doc)}
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
          onOpenLegal={(doc) => void legalDoc.open(doc)}
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
      {user && shareEnabled && (
        <button type="button" className="rounded-full border border-rule px-3 py-1.5 text-ink hover:border-stamp-blue/50"
                onClick={() => setShareManageOpen(true)} data-testid="share-manage-toggle">
          分享管理
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
                                  onClick={() => void preview.open(docId, doc.source)}>预览</button>
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

      <ChunkPreviewModal preview={preview} />

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
            onOpenLegal={(doc) => void legalDoc.open(doc)}
          />
        </Modal>
      )}
      {legalModal}
      <SupportModals support={support} />
    </div>
  )
}
