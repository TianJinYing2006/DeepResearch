/** 历史任务面板（R4b：从 AccountPanel 迁出，自带数据加载/筛选/分页/报告预览）。 */
import { useEffect, useState } from 'react'
import Modal from '../../components/Modal'
import { ReportView } from '../../components/ReportView'
import ShareModal from '../../components/ShareModal'
import { SkeletonRows } from '../../components/ui'
import { csrfHeaders, readErrorMessage } from '../../lib/api'
import { filenameFromDisposition } from '../../lib/download'
import { formatDateTime } from '../../lib/format'
import type { RunBrief } from '../../types/api'

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

/** 需求 22：可一键重试的终态（与后端 RETRYABLE_STATUSES 同口径） */
const RETRYABLE_STATUSES = new Set(['FAILED', 'LOST', 'TIMED_OUT'])

export default function HistoryPanel({ open, shareEnabled = false }: { open: boolean; shareEnabled?: boolean }) {
  const [history, setHistory] = useState<RunBrief[] | null>(null)
  const [historyError, setHistoryError] = useState('')
  const [historyAppendError, setHistoryAppendError] = useState('')
  const [historyStatus, setHistoryStatus] = useState('')
  const [historyQuery, setHistoryQuery] = useState('')
  const [historyArchived, setHistoryArchived] = useState(false)
  const [historyActionError, setHistoryActionError] = useState('')
  const [historyBusyId, setHistoryBusyId] = useState<string | null>(null)
  const [renamingId, setRenamingId] = useState<string | null>(null)
  const [renameValue, setRenameValue] = useState('')
  const [historyOffset, setHistoryOffset] = useState(0)
  const [historyHasMore, setHistoryHasMore] = useState(false)
  const [historyLoadingMore, setHistoryLoadingMore] = useState(false)
  const [preview, setPreview] = useState<{ runId: string; markdown: string } | null>(null)
  const [previewError, setPreviewError] = useState('')
  const [previewLoadingId, setPreviewLoadingId] = useState<string | null>(null)
  const [previewExporting, setPreviewExporting] = useState(false)
  // 需求 26：历史报告分享（复用 ShareModal：创建/复制/撤销/永久确认）
  const [shareRunId, setShareRunId] = useState<string | null>(null)

  async function loadHistory(reset: boolean, statusOverride?: string) {
    const status = statusOverride !== undefined ? statusOverride : historyStatus
    const nextOffset = reset ? 0 : historyOffset
    const params = new URLSearchParams({ limit: String(HISTORY_PAGE_SIZE), offset: String(nextOffset) })
    if (status) params.set('status', status)
    if (historyQuery.trim()) params.set('q', historyQuery.trim())
    if (historyArchived) params.set('archived', 'true')
    setHistoryLoadingMore(!reset)
    try {
      const response = await fetch(`/api/runs?${params.toString()}`)
      if (!response.ok) {
        const message = await readErrorMessage(response)
        // R3（审计 U12）：翻页失败只提示，不清掉已加载列表
        if (reset) setHistoryError(message)
        else setHistoryAppendError(message)
        return
      }
      const body = (await response.json()) as { runs: RunBrief[] }
      setHistoryError('')
      setHistoryAppendError('')
      setHistory((previous) => (reset ? body.runs : [...(previous ?? []), ...body.runs]))
      setHistoryOffset(nextOffset + body.runs.length)
      setHistoryHasMore(body.runs.length === HISTORY_PAGE_SIZE)
    } catch {
      if (reset) setHistoryError('网络错误，请重试')
      else setHistoryAppendError('网络错误，请重试')
    } finally {
      setHistoryLoadingMore(false)
    }
  }

  // 打开时才拉取（关闭保留已有数据）；每次重新打开都从头刷新
  useEffect(() => {
    if (!open) return
    setHistory(null)
    setHistoryError('')
    setHistoryAppendError('')
    setHistoryActionError('')
    void loadHistory(true)
    // eslint 未启用；按「打开」触发即可
  }, [open]) // eslint-disable-line react-hooks/exhaustive-deps

  // 需求 22：搜索输入防抖 400ms + 归档开关即时刷新（打开前的变更不触发）
  useEffect(() => {
    if (!open) return
    const timer = window.setTimeout(() => {
      setHistory(null)
      setHistoryAppendError('')
      void loadHistory(true)
    }, 400)
    return () => window.clearTimeout(timer)
  }, [historyQuery, historyArchived]) // eslint-disable-line react-hooks/exhaustive-deps

  /** 需求 22：写操作统一入口（CSRF + 错误提示 + 成功后刷新列表）。 */
  async function mutateRun(runId: string, path: string, body?: unknown, method = 'POST') {
    if (historyBusyId !== null) return
    setHistoryActionError('')
    setHistoryBusyId(runId)
    try {
      const response = await fetch(`/api/runs/${runId}${path}`, {
        method,
        headers: { 'Content-Type': 'application/json', ...csrfHeaders() },
        body: body === undefined ? undefined : JSON.stringify(body),
      })
      if (!response.ok) {
        setHistoryActionError(await readErrorMessage(response))
        return
      }
      setRenamingId(null)
      await loadHistory(true)
    } catch {
      setHistoryActionError('网络错误，请重试')
    } finally {
      setHistoryBusyId(null)
    }
  }

  // R3（审计 U13/U38）：加载态 + 异常兜底 + 防并发重复请求
  async function openReport(runId: string) {
    if (previewLoadingId !== null) return
    setPreviewError('')
    setPreview(null)
    setPreviewLoadingId(runId)
    try {
      const response = await fetch(`/api/research/${runId}/report?format=md`)
      if (!response.ok) {
        setPreviewError(await readErrorMessage(response))
        return
      }
      setPreview({ runId, markdown: await response.text() })
    } catch {
      setPreviewError('网络错误，请重试')
    } finally {
      setPreviewLoadingId(null)
    }
  }

  /** 历史报告导出（走后端导出端点：正文 + 审计元数据 + 引用清单）。 */
  async function exportPreview(runId: string) {
    if (previewExporting) return
    setPreviewExporting(true)
    setPreviewError('')
    try {
      const response = await fetch(`/api/research/${runId}/report?format=md`)
      if (!response.ok) {
        setPreviewError(await readErrorMessage(response))
        return
      }
      const blob = await response.blob()
      const filename = filenameFromDisposition(response.headers.get('content-disposition'))
        ?? `deepresearch-${runId}.md`
      const url = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = filename
      document.body.appendChild(anchor)
      anchor.click()
      anchor.remove()
      URL.revokeObjectURL(url)
    } catch {
      setPreviewError('网络错误，请重试')
    } finally {
      setPreviewExporting(false)
    }
  }

  if (!open) return null

  return (
    <>
      <div className="surface-card-muted mt-2 w-full p-3 animate-rise" data-testid="history-panel">
        <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
          <h3 className="text-[11px] font-medium text-ink-muted">历史任务</h3>
          <div className="flex flex-wrap items-center gap-2 text-[11px] text-ink-muted">
            <input
              className="w-36 rounded border border-rule bg-rule/40 px-2 py-1 text-[11px] text-ink placeholder:text-ink-muted/70"
              data-testid="history-search"
              placeholder="搜索主题…"
              value={historyQuery}
              onChange={(event) => setHistoryQuery(event.target.value)}
            />
            <label className="flex items-center gap-1">
              <input
                type="checkbox"
                data-testid="history-archived-toggle"
                checked={historyArchived}
                onChange={(event) => setHistoryArchived(event.target.checked)}
              />
              已归档
            </label>
            <label className="flex items-center gap-1">
              状态
              <select
                className="rounded border border-rule bg-rule/40 px-2 py-1 text-[11px] text-ink [&>option]:bg-sheet [&>option]:text-ink"
                data-testid="history-status-filter"
                value={historyStatus}
                onChange={(event) => {
                  setHistoryStatus(event.target.value)
                  setHistory(null)
                  setHistoryAppendError('')
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
        </div>
        {historyError && <p className="text-stamp-amber" data-testid="history-error">{historyError}</p>}
        {historyActionError && (
          <p className="text-stamp-amber" data-testid="history-action-error">{historyActionError}</p>
        )}
        {historyAppendError && (
          <p className="flex items-center gap-2 text-stamp-amber" data-testid="history-append-error">
            {historyAppendError}
            <button type="button" className="underline hover:text-stamp-amber"
                    data-testid="history-retry" onClick={() => void loadHistory(false)}>重试</button>
          </p>
        )}
        {!historyError && history === null && (
          <div role="status" aria-live="polite">
            <span className="sr-only">加载中…</span>
            <SkeletonRows rows={4} />
          </div>
        )}
        {history && history.length === 0 && !historyError && (
          <p className="text-ink-muted" data-testid="history-empty">暂无历史任务</p>
        )}
        {history && history.length > 0 && (
          <ul className="space-y-2">
            {history.map((item) => (
              <li key={item.run_id} className="flex flex-wrap items-center justify-between gap-2 text-ink">
                <span className="min-w-0 flex-1 truncate">
                  <code className="mr-2 text-[11px] text-ink-muted">{item.run_id}</code>
                  {renamingId === item.run_id ? (
                    <input
                      className="w-48 rounded border border-rule bg-rule/40 px-1.5 py-0.5 text-ink"
                      data-testid="history-rename-input"
                      autoFocus
                      value={renameValue}
                      onChange={(event) => setRenameValue(event.target.value)}
                      onKeyDown={(event) => {
                        if (event.key === 'Enter') {
                          void mutateRun(item.run_id, '', { topic: renameValue }, 'PATCH')
                        }
                        if (event.key === 'Escape') setRenamingId(null)
                      }}
                    />
                  ) : (
                    item.topic
                  )}
                </span>
                <span className="flex flex-wrap items-center gap-2 text-[11px] text-ink-muted">
                  {item.pinned_at && (
                    <span className="rounded border border-stamp-blue/40 px-1 text-stamp-blue">置顶</span>
                  )}
                  {STATUS_LABELS[item.status] ?? item.status}
                  {item.moderation_status && item.moderation_status !== 'cleared' && (
                    <span className="rounded border border-stamp-amber/40 px-1 text-stamp-amber"
                          data-testid="flagged-badge">
                      {item.moderation_status === 'under_review' ? '审核中' : '已标记'}
                    </span>
                  )}
                  {item.created_at && <time dateTime={item.created_at}>{formatDateTime(item.created_at)}</time>}
                  {renamingId === item.run_id ? (
                    <>
                      <button type="button" className="underline hover:text-ink"
                              data-testid="history-rename-save"
                              disabled={historyBusyId !== null || !renameValue.trim()}
                              onClick={() => void mutateRun(item.run_id, '', { topic: renameValue }, 'PATCH')}>
                        保存
                      </button>
                      <button type="button" className="underline hover:text-ink"
                              onClick={() => setRenamingId(null)}>取消</button>
                    </>
                  ) : (
                    <button type="button" className="underline hover:text-ink" data-testid="history-rename"
                            disabled={historyBusyId !== null}
                            onClick={() => { setRenamingId(item.run_id); setRenameValue(item.topic) }}>
                      重命名
                    </button>
                  )}
                  <button type="button" className="underline hover:text-ink" data-testid="history-pin"
                          disabled={historyBusyId !== null}
                          onClick={() => void mutateRun(item.run_id, item.pinned_at ? '/unpin' : '/pin')}>
                    {item.pinned_at ? '取消置顶' : '置顶'}
                  </button>
                  <button type="button" className="underline hover:text-ink" data-testid="history-archive"
                          disabled={historyBusyId !== null}
                          onClick={() => void mutateRun(item.run_id, item.archived_at ? '/unarchive' : '/archive')}>
                    {item.archived_at ? '取消归档' : '归档'}
                  </button>
                  {RETRYABLE_STATUSES.has(item.status) && (
                    <button type="button" className="underline hover:text-ink"
                            data-testid="history-run-retry"
                            disabled={historyBusyId !== null}
                            onClick={() => void mutateRun(item.run_id, '/retry')}>
                      重试
                    </button>
                  )}
                  {item.has_report && (
                    <button type="button" className="underline hover:text-ink"
                            disabled={previewLoadingId !== null}
                            onClick={() => void openReport(item.run_id)}>
                      {previewLoadingId === item.run_id ? '加载中…' : '查看报告'}
                    </button>
                  )}
                </span>
              </li>
            ))}
          </ul>
        )}
        {!historyError && history && history.length > 0 && historyHasMore && !historyAppendError && (
          <button type="button" className="mt-3 text-[11px] text-ink-muted underline hover:text-ink"
                  data-testid="history-load-more" disabled={historyLoadingMore}
                  onClick={() => void loadHistory(false)}>
            {historyLoadingMore ? '加载中…' : '加载更多'}
          </button>
        )}
      </div>

      {(preview || previewError) && (
        <Modal
          onClose={() => { setPreview(null); setPreviewError('') }}
          labelledBy="preview-title"
          testId="history-preview"
          overlayClassName="z-50 flex items-start justify-center overflow-y-auto bg-ink/35 p-4 "
          panelClassName="surface-card my-6 w-full max-w-3xl p-6"
        >
          <div className="flex items-center justify-between">
            <h3 id="preview-title" className="text-sm font-semibold text-ink">
              历史报告 {preview?.runId}
            </h3>
            <div className="flex items-center gap-3">
              {preview && shareEnabled && (
                <button type="button" className="text-xs text-ink-muted underline hover:text-ink"
                        data-testid="history-share"
                        onClick={() => setShareRunId(preview.runId)}>
                  分享
                </button>
              )}
              {preview && (
                <button type="button" className="text-xs text-ink-muted underline hover:text-ink"
                        data-testid="history-export" disabled={previewExporting}
                        onClick={() => void exportPreview(preview.runId)}>
                  {previewExporting ? '导出中…' : '导出 .md'}
                </button>
              )}
              <button type="button" className="text-xs text-ink-muted hover:text-ink"
                      onClick={() => { setPreview(null); setPreviewError('') }}>关闭</button>
            </div>
          </div>
          {previewError && <p role="alert" className="mt-3 text-sm text-stamp-red">{previewError}</p>}
          {preview && (
            <div className="mt-4">
              <ReportView report={preview.markdown} />
            </div>
          )}
        </Modal>
      )}
      {shareRunId && <ShareModal runId={shareRunId} onClose={() => setShareRunId(null)} />}
    </>
  )
}
