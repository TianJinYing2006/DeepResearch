import { useCallback, useEffect, useState } from 'react'
import { csrfHeaders, readErrorMessage } from '../lib/api'

/** 会话条目（后端 `/api/auth/sessions` 形状；需求 20 P0-3）。 */
export type SessionItem = {
  session_id: string
  current: boolean
  created_at: string
  last_seen_at: string
  ip: string | null
  user_agent: string | null
}

/** 从 UA 提取可读的设备/浏览器摘要（展示用，不做安全判定）。 */
export function deviceLabel(ua: string | null): string {
  if (!ua) return '未知设备'
  const os = /iPhone/i.test(ua)
    ? 'iPhone'
    : /iPad/i.test(ua)
      ? 'iPad'
      : /Android/i.test(ua)
        ? 'Android'
        : /Macintosh|Mac OS X/i.test(ua)
          ? 'macOS'
          : /Windows/i.test(ua)
            ? 'Windows'
            : /Linux/i.test(ua)
              ? 'Linux'
              : '未知系统'
  const browser = /Edg\//i.test(ua)
    ? 'Edge'
    : /Chrome\//i.test(ua)
      ? 'Chrome'
      : /Safari\//i.test(ua)
        ? 'Safari'
        : /Firefox\//i.test(ua)
          ? 'Firefox'
          : '未知浏览器'
  return `${os} · ${browser}`
}

function timeLabel(iso: string): string {
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString()
}

type Props = {
  /** 当前会话被撤销（退出本机）时通知父组件清理登录态。 */
  onSignedOut: () => void
}

/** 会话与安全面板（需求 20 P0-3）：设备列表 / 单条撤销 / 退出其他所有设备。
 *
 * 数据来自后端既有 API（P1-10）：终止**其他**会话需重认证（当前密码），
 * 与 GitHub/Google 的设备管理形态一致。 */
export default function SecurityPanel({ onSignedOut }: Props) {
  const [sessions, setSessions] = useState<SessionItem[] | null>(null)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    try {
      const response = await fetch('/api/auth/sessions')
      if (!response.ok) {
        setError(await readErrorMessage(response))
        setSessions(null)
        return
      }
      const body = (await response.json()) as { sessions: SessionItem[] }
      setSessions(body.sessions)
      setError('')
    } catch {
      setError('网络错误，请重试')
      setSessions(null)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  async function revoke(session: SessionItem) {
    let password = ''
    if (session.current) {
      if (!window.confirm('退出当前设备？将立即退出登录。')) return
    } else {
      password = window.prompt('退出该设备需要输入当前密码确认：') ?? ''
      if (!password) return
    }
    setBusy(true)
    setNotice('')
    try {
      const response = await fetch(`/api/auth/sessions/${encodeURIComponent(session.session_id)}`, {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json', ...csrfHeaders() },
        body: JSON.stringify(password ? { password } : {}),
      })
      if (!response.ok) {
        setNotice(await readErrorMessage(response))
        return
      }
      if (session.current) {
        onSignedOut()
        return
      }
      setNotice('该设备已退出')
      await load()
    } catch {
      setNotice('网络错误，请重试')
    } finally {
      setBusy(false)
    }
  }

  async function revokeOthers() {
    const password = window.prompt('退出其他所有设备需要输入当前密码确认：')
    if (!password) return
    setBusy(true)
    setNotice('')
    try {
      const response = await fetch('/api/auth/sessions', {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json', ...csrfHeaders() },
        body: JSON.stringify({ password }),
      })
      if (!response.ok) {
        setNotice(await readErrorMessage(response))
        return
      }
      const body = (await response.json()) as { revoked: number }
      setNotice(`已退出其他设备（${body.revoked} 个）`)
      await load()
    } catch {
      setNotice('网络错误，请重试')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="surface-card-muted mt-2 w-full max-w-md p-3 sm:ml-auto" data-testid="security-panel">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-[11px] font-medium text-ink-muted">会话与安全</h3>
        <button type="button" className="text-[11px] text-ink-muted underline hover:text-ink"
                data-testid="security-refresh" onClick={() => void load()}>
          刷新
        </button>
      </div>

      {error && (
        <p role="alert" className="text-stamp-amber" data-testid="security-error">会话列表不可用：{error}</p>
      )}
      {!error && sessions === null && (
        <p role="status" aria-live="polite" className="text-ink-muted">加载中…</p>
      )}
      {!error && sessions && sessions.length === 0 && (
        <p className="text-ink-muted">没有活跃会话</p>
      )}
      {!error && sessions && sessions.length > 0 && (
        <ul className="max-h-56 space-y-2 overflow-y-auto pr-1"
            tabIndex={0} role="region" aria-label="登录设备列表">
          {sessions.map((session) => (
            <li key={session.session_id} data-testid="session-item"
                className="rounded-lg border border-rule bg-rule/40 px-3 py-2 text-[11px]">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="min-w-0 flex-1 truncate text-ink" title={session.user_agent ?? ''}>
                  {deviceLabel(session.user_agent)}
                </span>
                {session.current && (
                  <span className="rounded-full border border-stamp-green/40 bg-stamp-green/10 px-2 py-0.5 text-stamp-green"
                        data-testid="session-current">当前设备</span>
                )}
                <button type="button" className="text-ink-muted underline hover:text-stamp-red disabled:opacity-60"
                        data-testid="session-revoke" disabled={busy}
                        onClick={() => void revoke(session)}>
                  退出
                </button>
              </div>
              <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-ink-muted">
                <span>IP：{session.ip || '未知'}</span>
                <span>最近活跃：{timeLabel(session.last_seen_at)}</span>
                <span>登录于：{timeLabel(session.created_at)}</span>
              </div>
            </li>
          ))}
        </ul>
      )}

      <div className="mt-3 flex items-center justify-between gap-2">
        <button type="button"
                className="rounded-full border border-rule px-3 py-1 text-[11px] text-ink hover:border-stamp-red/50 disabled:opacity-60"
                data-testid="session-revoke-others" disabled={busy} onClick={() => void revokeOthers()}>
          退出其他所有设备
        </button>
        {busy && <span role="status" aria-live="polite" className="text-ink-muted">处理中…</span>}
      </div>
      {notice && (
        <p role="status" aria-live="polite" className="mt-2 text-ink-muted" data-testid="security-notice">{notice}</p>
      )}
    </div>
  )
}
