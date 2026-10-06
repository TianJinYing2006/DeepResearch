import { useCallback, useEffect, useState } from 'react'
import { csrfHeaders, readErrorMessage } from '../lib/api'
import Modal from './Modal'

type Props = { runId: string; onClose: () => void }

type ShareState = {
  active: boolean
  share_id?: string
  permanent?: boolean
  expires_at?: string | null
  last_accessed_at?: string | null
  access_count?: number
}

/** 需求 26：报告只读分享面板（创建/复制/撤销/重新生成；永久需二次确认）。 */
export default function ShareModal({ runId, onClose }: Props) {
  const [state, setState] = useState<ShareState | null>(null)
  const [url, setUrl] = useState('')
  const [expiresDays, setExpiresDays] = useState('7')
  const [permanentConfirmed, setPermanentConfirmed] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [copied, setCopied] = useState(false)

  const loadState = useCallback(async () => {
    try {
      const response = await fetch(`/api/runs/${runId}/share`)
      if (!response.ok) {
        setError(await readErrorMessage(response))
        return
      }
      setState((await response.json()) as ShareState)
    } catch {
      setError('网络错误，请重试')
    }
  }, [runId])

  useEffect(() => { void loadState() }, [loadState])

  const permanent = expiresDays === '0'
  const shareUrl = url ? `${window.location.origin}${url}` : ''

  async function create() {
    if (permanent && !permanentConfirmed) return
    setBusy(true)
    setError('')
    try {
      const response = await fetch(`/api/runs/${runId}/share`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...csrfHeaders() },
        body: JSON.stringify({ expires_days: Number(expiresDays) }),
      })
      if (!response.ok) {
        setError(await readErrorMessage(response))
        return
      }
      const body = (await response.json()) as { url: string }
      setUrl(body.url)
      await loadState()
    } catch {
      setError('网络错误，请重试')
    } finally {
      setBusy(false)
    }
  }

  async function revoke() {
    setBusy(true)
    setError('')
    try {
      const response = await fetch(`/api/runs/${runId}/share`, {
        method: 'DELETE',
        headers: { ...csrfHeaders() },
      })
      if (!response.ok) {
        setError(await readErrorMessage(response))
        return
      }
      setUrl('')
      setCopied(false)
      await loadState()
    } catch {
      setError('网络错误，请重试')
    } finally {
      setBusy(false)
    }
  }

  async function copy() {
    try {
      await navigator.clipboard.writeText(shareUrl)
      setCopied(true)
    } catch {
      setError('复制失败，请手动选择链接复制')
    }
  }

  return (
    <Modal
      onClose={onClose}
      labelledBy="share-title"
      testId="share-modal"
      overlayClassName="z-50 flex items-start justify-center overflow-y-auto bg-ink/35 p-4"
      panelClassName="surface-card my-6 w-full max-w-lg p-6"
    >
      <div className="flex items-center justify-between">
        <h3 id="share-title" className="text-sm font-semibold text-ink">分享报告（只读）</h3>
        <button type="button" className="text-xs text-ink-muted hover:text-ink"
                data-testid="share-close" onClick={onClose}>关闭</button>
      </div>
      <p className="mt-2 text-[11px] leading-5 text-ink-muted">
        拿到链接即可查看（无需登录）。默认 7 天有效；可随时撤销；撤销后链接立即失效。
      </p>

      {url && (
        <div className="mt-4">
          <p className="text-[11px] text-ink-muted">链接已生成（仅显示这一次，请复制保存）：</p>
          <div className="mt-1 flex items-center gap-2">
            <input readOnly value={shareUrl} data-testid="share-url"
                   className="field-control flex-1 text-[11px]" onFocus={(e) => e.target.select()} />
            <button type="button" className="secondary-button !px-3 !py-2 text-xs"
                    data-testid="share-copy" onClick={() => void copy()}>
              {copied ? '已复制' : '复制'}
            </button>
          </div>
        </div>
      )}

      {state?.active && (
        <p className="mt-3 text-[11px] text-ink-muted" data-testid="share-status">
          当前链接：{state.permanent ? '永久有效' : `到期 ${state.expires_at?.slice(0, 10) ?? '—'}`}
          {' · '}访问 {state.access_count ?? 0} 次
          {state.last_accessed_at ? ` · 最近 ${state.last_accessed_at.slice(0, 16).replace('T', ' ')}` : ''}
        </p>
      )}

      {!url && (
        <>
          <label className="field-label mt-4" htmlFor="share-expiry">有效期</label>
          <select id="share-expiry" className="field-control" data-testid="share-expiry"
                  value={expiresDays} onChange={(event) => setExpiresDays(event.target.value)}>
            <option value="1">1 天</option>
            <option value="7">7 天（默认）</option>
            <option value="30">30 天</option>
            <option value="0">永久有效</option>
          </select>
          {permanent && (
            <label className="mt-3 flex items-start gap-2 text-[11px] text-stamp-amber">
              <input type="checkbox" className="mt-0.5" checked={permanentConfirmed}
                     data-testid="share-permanent-confirm"
                     onChange={(event) => setPermanentConfirmed(event.target.checked)} />
              <span>永久链接不会自动失效，转发出去后无法收回（只能手动撤销）——我已了解并确认。</span>
            </label>
          )}
        </>
      )}

      {error && (
        <p role="alert" data-testid="share-error" className="mt-4 text-sm text-stamp-red">{error}</p>
      )}

      <div className="mt-5 flex gap-3">
        {!url && (
          <button type="button" className="primary-button flex-1"
                  data-testid="share-create"
                  disabled={busy || (permanent && !permanentConfirmed)}
                  onClick={() => void create()}>
            {busy ? '处理中…' : state?.active ? '重新生成链接' : '生成分享链接'}
          </button>
        )}
        {state?.active && (
          <button type="button" className="secondary-button flex-1"
                  data-testid="share-revoke" disabled={busy}
                  onClick={() => void revoke()}>
            撤销当前链接
          </button>
        )}
      </div>
    </Modal>
  )
}
