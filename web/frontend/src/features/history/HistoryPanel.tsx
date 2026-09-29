/** 历史任务面板（R4b：从 AccountPanel 迁出，自带数据加载/筛选/分页/报告预览）。 */
import { useEffect, useState } from 'react'
import Modal from '../../components/Modal'
import { ReportView } from '../../components/ReportView'
import { readErrorMessage } from '../../lib/api'
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

export default function HistoryPanel({ open }: { open: boolean }) {
  const [history, setHistory] = useState<RunBrief[] | null>(null)
  const [historyError, setHistoryError] = useState('')
  const [historyAppendError, setHistoryAppendError] = useState('')
  const [historyStatus, setHistoryStatus] = useState('')
  const [historyOffset, setHistoryOffset] = useState(0)
  const [historyHasMore, setHistoryHasMore] = useState(false)
  const [historyLoadingMore, setHistoryLoadingMore] = useState(false)
  const [preview, setPreview] = useState<{ runId: string; markdown: string } | null>(null)
  const [previewError, setPreviewError] = useState('')
  const [previewLoadingId, setPreviewLoadingId] = useState<string | null>(null)

  async function loadHistory(reset: boolean, statusOverride?: string) {
    const status = statusOverride !== undefined ? statusOverride : historyStatus
    const nextOffset = reset ? 0 : historyOffset
    const params = new URLSearchParams({ limit: String(HISTORY_PAGE_SIZE), offset: String(nextOffset) })
    if (status) params.set('status', status)
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
    void loadHistory(true)
    // eslint 未启用；按「打开」触发即可
  }, [open]) // eslint-disable-line react-hooks/exhaustive-deps

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

  if (!open) return null

  return (
    <>
      <div className="surface-card-muted mt-2 w-full p-3" data-testid="history-panel">
        <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
          <h3 className="text-[11px] font-semibold uppercase tracking-[0.16em] text-emerald-100/50">历史任务</h3>
          <label className="flex items-center gap-1 text-[11px] text-emerald-100/70">
            状态
            <select
              className="rounded border border-white/10 bg-black/30 px-2 py-1 text-[11px] text-emerald-50 [&>option]:bg-[#0d1816] [&>option]:text-emerald-50"
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
        {historyError && <p className="text-amber-200/80" data-testid="history-error">{historyError}</p>}
        {historyAppendError && (
          <p className="flex items-center gap-2 text-amber-200/80" data-testid="history-append-error">
            {historyAppendError}
            <button type="button" className="underline hover:text-amber-100"
                    data-testid="history-retry" onClick={() => void loadHistory(false)}>重试</button>
          </p>
        )}
        {!historyError && history === null && <p className="text-emerald-100/60">加载中…</p>}
        {history && history.length === 0 && !historyError && <p className="text-emerald-100/60">暂无历史任务</p>}
        {history && history.length > 0 && (
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
          <button type="button" className="mt-3 text-[11px] text-emerald-200/70 underline hover:text-emerald-100"
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
    </>
  )
}
