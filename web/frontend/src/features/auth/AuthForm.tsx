import { useState } from 'react'
import { readErrorMessage } from '../../lib/api'
import type { SessionUser } from '../../types/api'

type Mode = 'login' | 'register'

type Props = {
  /** 表单控件 id 前缀（auth / invite）：两个入口互斥渲染，与既有 e2e 契约对齐。 */
  idPrefix: string
  /** 标题元素 id（Modal 的 aria-labelledby 指向它）。 */
  titleId: string
  initialMode?: Mode
  /** 邀请链接预填的邀请码（可空）。 */
  inviteFromUrl?: string
  /** 顶部提示（如「账号已注销」），仅登录门使用。 */
  notice?: string
  /** 注册模式副标题覆盖（邀请弹窗使用「邀请码已从链接预填」）。 */
  subtitle?: string
  onAuthed: (user: SessionUser) => void
  onOpenLegal: (doc: 'privacy' | 'terms') => void
}

/** 登录 / 邀请制注册表单（自包含状态与提交逻辑；登录门与邀请弹窗共用）。
 *
 * UX 基线（参考 Linear/Clerk/Stripe）：单一主行动、清晰标签、邮箱 autofill、
 * 密码显示/隐藏（aria-pressed）、错误 role=alert 且与输入建立 aria-describedby。 */
export default function AuthForm({
  idPrefix,
  titleId,
  initialMode = 'login',
  inviteFromUrl = '',
  notice = '',
  subtitle,
  onAuthed,
  onOpenLegal,
}: Props) {
  const [mode, setMode] = useState<Mode>(initialMode)
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [inviteCode, setInviteCode] = useState(inviteFromUrl)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const title = mode === 'login' ? '登录 DeepResearch' : '邀请制注册'
  const subtitleText =
    subtitle && mode === 'register'
      ? subtitle
      : mode === 'login'
        ? '使用邮箱与密码继续'
        : '邀请码一次性使用，由管理员发放'

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setBusy(true)
    setError('')
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
        setError(await readErrorMessage(response))
        return
      }
      const body = (await response.json()) as { user: SessionUser }
      setPassword('')
      setInviteCode('')
      setShowPassword(false)
      onAuthed(body.user)
    } catch {
      setError('网络错误，请重试')
    } finally {
      setBusy(false)
    }
  }

  return (
    <form onSubmit={(event) => void submit(event)}>
      <h2 id={titleId} className="text-xl font-semibold tracking-tight text-ink">{title}</h2>
      <p className="mt-1.5 text-[13px] text-ink-muted">{subtitleText}</p>

      {notice && (
        <p role="status" data-testid="auth-notice"
           className="mt-4 rounded-lg border border-stamp-green/30 bg-stamp-green/10 px-3 py-2 text-xs text-ink">
          {notice}
        </p>
      )}

      <label className="field-label mt-6" htmlFor={`${idPrefix}-email`}>邮箱</label>
      <input id={`${idPrefix}-email`} name="email" type="email" autoComplete="email"
             className="field-control" placeholder="you@example.com"
             aria-invalid={error ? true : undefined}
             aria-describedby={error ? `${idPrefix}-error` : undefined}
             value={email} onChange={(event) => setEmail(event.target.value)} required />

      <label className="field-label mt-4" htmlFor={`${idPrefix}-password`}>密码</label>
      <div className="relative">
        <input id={`${idPrefix}-password`} name="password"
               type={showPassword ? 'text' : 'password'}
               autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
               className="field-control pr-12"
               aria-invalid={error ? true : undefined}
               aria-describedby={error ? `${idPrefix}-error` : undefined}
               value={password} onChange={(event) => setPassword(event.target.value)}
               minLength={12} required />
        <button type="button" tabIndex={0}
                className="absolute inset-y-0 right-0 flex w-12 items-center justify-center rounded-r-lg text-ink-muted transition hover:text-ink"
                aria-label={showPassword ? '隐藏密码' : '显示密码'}
                aria-pressed={showPassword}
                data-testid={`${idPrefix}-password-toggle`}
                onClick={() => setShowPassword((visible) => !visible)}>
          {showPassword ? <EyeOffIcon /> : <EyeIcon />}
        </button>
      </div>
      {mode === 'register' && (
        <p className="mt-1.5 text-[11px] text-ink-muted">至少 12 位；避免使用常见口令或邮箱账号名</p>
      )}

      {mode === 'register' && (
        <>
          <label className="field-label mt-4" htmlFor={`${idPrefix}-invite`}>邀请码</label>
          <input id={`${idPrefix}-invite`} name="invite_code"
                 className="field-control" autoComplete="off" spellCheck={false}
                 data-testid={idPrefix === 'invite' ? 'invite-code-input' : undefined}
                 value={inviteCode} onChange={(event) => setInviteCode(event.target.value)} required />
        </>
      )}

      {error && (
        <p id={`${idPrefix}-error`} role="alert" data-testid="auth-error"
           className="mt-4 text-sm text-stamp-red">{error}</p>
      )}

      <button type="submit" className="primary-button mt-6 w-full" disabled={busy}>
        {busy ? (mode === 'login' ? '登录中…' : '提交中…') : mode === 'login' ? '登录' : '注册并登录'}
      </button>
      <button type="button" className="mt-3 w-full text-xs text-ink-muted transition hover:text-ink"
              onClick={() => { setMode(mode === 'login' ? 'register' : 'login'); setError('') }}>
        {mode === 'login' ? '有邀请码？去注册' : '已有账号？去登录'}
      </button>
      <p className="mt-4 text-center text-[11px] text-ink-muted">
        {mode === 'register' && '注册即表示同意'}
        <button type="button" className="mx-1 underline hover:text-ink"
                onClick={() => onOpenLegal('terms')}>用户协议</button>
        与
        <button type="button" className="mx-1 underline hover:text-ink"
                onClick={() => onOpenLegal('privacy')}>隐私政策</button>
      </p>
    </form>
  )
}

function EyeIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor"
         strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className="h-[18px] w-[18px]">
      <path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12Z" />
      <circle cx="12" cy="12" r="3.2" />
    </svg>
  )
}

function EyeOffIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor"
         strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className="h-[18px] w-[18px]">
      <path d="M2.5 12S6 5.5 12 5.5c1.9 0 3.5.6 4.9 1.4M21.5 12s-3.5 6.5-9.5 6.5c-1.9 0-3.5-.6-4.9-1.4" />
      <path d="M4 4l16 16" />
      <path d="M9.9 9.9a3.2 3.2 0 0 0 4.2 4.2" />
    </svg>
  )
}
