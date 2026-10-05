import { useCallback, useEffect, useState } from 'react'
import { csrfHeaders, readErrorMessage } from '../lib/api'
import Modal from './Modal'

type Props = { onClose: () => void }

type ShareRow = {
  share_id: string
  run_id: string
  topic: string | null
  created_at: string | null
  permanent: boolean
  expires_at: string | null
  last_accessed_at: string | null
  access_count: number
}

/** 需求 26：已分享链接集中管理页（列表 + 撤销）。
 *
 * token 明文不可恢复（只存 hash）——此页只做状态盘点与撤销；
 * 重新生成请到对应报告/历史预览的「分享」入口。 */
export default function ShareManageModal({ onClose }: Props) {
  const [rows, setRows] = useState<ShareRow[] | null>(null)
  const [error, setError] = useState('')
  const [busyId, setBusyId] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const response = await fetch('/api/shares')
      if (!response.ok) {
        setError(await readErrorMessage(response))
        return
      }
      const body = (await response.json()) as { shares: ShareRow[] }
      setRows(body.shares)
    } catch {
      setError('网络错误，请重试')
    }
  }, [])

  useEffect(() => { void load() }, [load])

  async function revoke(row: ShareRow) {
    if (busyId !== null) return
    setBusyId(row.share_id)
    setError('')
    try {
      const response = await fetch(`/api/runs/${row.run_id}/share`, {
        method: 'DELETE',
        headers: { ...csrfHeaders() },
      })
      if (!response.ok) {
        setError(await readErrorMessage(response))
        return
      }
      await load()
    } catch {
      setError('网络错误，请重试')
    } finally {
      setBusyId(null)
    }
  }

  return (
    <Modal
      onClose={onClose}
      labelledBy="share-manage-title"
      testId="share-manage-modal"
      overlayClassName="z-50 flex items-start justify-center overflow-y-auto bg-ink/35 p-4"
      panelClassName="surface-card my-6 w-full max-w-2xl p-6"
    >
      <div className="flex items-center justify-between">
        <h3 id="share-manage-title" className="text-sm font-semibold text-ink">已分享链接</h3>
        <button type="button" className="text-xs text-ink-muted hover:text-ink"
                data-testid="share-manage-close" onClick={onClose}>关闭</button>
      </div>
      <p className="mt-2 text-[11px] leading-5 text-ink-muted">
        链接明文不可恢复（只存摘要）：此页用于盘点与撤销；重新生成请到对应报告的「分享」入口。
      </p>

      {error && <p role="alert" data-testid="share-manage-error"
                   className="mt-3 text-sm text-stamp-red">{error}</p>}
      {!error && rows === null && <p className="mt-3 text-sm text-ink-muted">加载中…</p>}
      {!error && rows !== null && rows.length === 0 && (
        <p className="mt-3 text-sm text-ink-muted" data-testid="share-manage-empty">暂无活跃分享链接</p>
      )}
      {!error && rows !== null && rows.length > 0 && (
        <ul className="mt-4 space-y-2">
          {rows.map((row) => (
            <li key={row.share_id} data-testid="share-manage-item"
                className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-rule bg-rule/30 px-3 py-2 text-xs">
              <span className="min-w-0 flex-1 truncate text-ink" title={row.topic ?? row.run_id}>
                {row.topic || row.run_id}
                <code className="ml-2 text-[10px] text-ink-muted">{row.run_id}</code>
              </span>
              <span className="tabular-nums text-ink-muted">
                {row.permanent ? '永久' : `到期 ${row.expires_at?.slice(0, 10) ?? '—'}`}
                {' · '}访问 {row.access_count} 次
                {row.last_accessed_at ? ` · 最近 ${row.last_accessed_at.slice(0, 16).replace('T', ' ')}` : ''}
              </span>
              <button type="button" className="text-stamp-red/80 underline hover:text-stamp-red"
                      data-testid="share-manage-revoke" disabled={busyId !== null}
                      onClick={() => void revoke(row)}>
                {busyId === row.share_id ? '撤销中…' : '撤销'}
              </button>
            </li>
          ))}
        </ul>
      )}
    </Modal>
  )
}
