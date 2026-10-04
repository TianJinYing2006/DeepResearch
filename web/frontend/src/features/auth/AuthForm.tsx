import { useEffect, useState } from 'react'
import { readErrorMessage } from '../../lib/api'
import type { SessionUser } from '../../types/api'

type Mode = 'login' | 'register' | 'forgot' | 'reset'
type ResetState = 'form' | 'invalid' | 'done'

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

/** 登录 / 邀请制注册 / 自助找回 / 重置密码表单（自包含状态与提交逻辑；登录门与邀请弹窗共用）。
 *
 * UX 基线（Linear/Clerk/Stripe + 需求 24 市面调研）：单一主行动、清晰标签、邮箱 autofill、
 * 密码显示/隐藏（aria-pressed）、错误 role=alert 且与输入建立 aria-describedby；
 * 找回流程防枚举中性文案、失效链接给出「重新申请」活路、密码规则常显并实时反馈（灰→绿）。 */
const RESET_HASH_RE = /^#reset=([A-Za-z0-9_-]{10,256})$/
const RESET_COOLDOWN_SECONDS = 60
const RESET_RULES_ADVISORY = '避免常见口令与邮箱账号名（提交时校验）'

function readResetToken(idPrefix: string): string {
  if (idPrefix !== 'auth') return ''
  const match = window.location.hash.match(RESET_HASH_RE)
  return match ? match[1] : ''
}

function clearResetHash(): void {
  if (RESET_HASH_RE.test(window.location.hash)) {
    window.history.replaceState(null, '', window.location.pathname + window.location.search)
  }
}

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
  const [resetToken] = useState(() => readResetToken(idPrefix))
  const [mode, setMode] = useState<Mode>(resetToken ? 'reset' : initialMode)
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [showConfirm, setShowConfirm] = useState(false)
  const [inviteCode, setInviteCode] = useState(inviteFromUrl)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [forgotSent, setForgotSent] = useState(false)
  const [resendLeft, setResendLeft] = useState(0)
  const [resetState, setResetState] = useState<ResetState>('form')

  useEffect(() => {
    if (resendLeft <= 0) return
    const timer = window.setTimeout(() => setResendLeft(resendLeft - 1), 1000)
    return () => window.clearTimeout(timer)
  }, [resendLeft])

  const title =
    mode === 'login'
      ? '登录 DeepResearch'
      : mode === 'register'
        ? '邀请制注册'
        : mode === 'forgot'
          ? forgotSent
            ? '查收重置邮件'
            : '找回密码'
          : resetState === 'done'
            ? '密码已重置'
            : resetState === 'invalid'
              ? '链接无效或已过期'
              : '设置新密码'

  const subtitleText =
    subtitle && mode === 'register'
      ? subtitle
      : mode === 'login'
        ? '使用邮箱与密码继续'
        : mode === 'register'
          ? '邀请码一次性使用，由管理员发放'
          : mode === 'forgot'
            ? forgotSent
              ? '如果该邮箱已注册，我们已发送重置链接'
              : '输入注册邮箱，我们会发送重置链接'
            : resetState === 'done'
              ? '所有设备已退出登录，请用新密码重新登录'
              : resetState === 'invalid'
                ? '请重新申请一封重置邮件'
                : '链接 30 分钟内有效，仅可使用一次'

  const resetRules = [
    { label: '至少 12 位', ok: password.length >= 12 },
    {
      label: '不含服务名 deepresearch',
      ok: password.length > 0 && !password.toLowerCase().includes('deepresearch'),
    },
  ]
  const resetReady = password.length >= 12 && confirm === password

  function goLogin() {
    clearResetHash()
    setMode('login')
    setError('')
    setPassword('')
    setConfirm('')
    setForgotSent(false)
    setResetState('form')
  }

  function goForgot() {
    setMode('forgot')
    setError('')
    setForgotSent(false)
    setResetState('form')
  }

  async function sendForgot(): Promise<boolean> {
    const response = await fetch('/api/auth/forgot', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email }),
    })
    if (!response.ok) {
      setError(await readErrorMessage(response))
      return false
    }
    setForgotSent(true)
    setResendLeft(RESET_COOLDOWN_SECONDS)
    return true
  }

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setBusy(true)
    setError('')
    try {
      if (mode === 'forgot') {
        await sendForgot()
        return
      }
      if (mode === 'reset') {
        const response = await fetch('/api/auth/reset', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ token: resetToken, new_password: password }),
        })
        if (!response.ok) {
          if (response.status === 422) {
            setResetState('invalid')
            clearResetHash()
            return
          }
          setError(await readErrorMessage(response))
          return
        }
        setPassword('')
        setConfirm('')
        setShowPassword(false)
        setShowConfirm(false)
        setResetState('done')
        clearResetHash()
        return
      }
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

  async function resend() {
    if (resendLeft > 0 || busy) return
    setBusy(true)
    setError('')
    try {
      await sendForgot()
    } catch {
      setError('网络错误，请重试')
    } finally {
      setBusy(false)
    }
  }

  if (mode === 'forgot' && forgotSent) {
    return (
      <div>
        <h2 id={titleId} className="text-xl font-semibold tracking-tight text-balance text-ink">{title}</h2>
        <p className="mt-1.5 text-[13px] text-pretty text-ink-muted">{subtitleText}</p>
        <p role="status" data-testid="auth-forgot-sent"
           className="mt-4 rounded-lg border border-stamp-green/30 bg-stamp-green/10 px-3 py-2 text-xs text-ink">
          链接 30 分钟内有效，仅可使用一次；没收到请检查垃圾邮件。
        </p>
        {error && (
          <p id={`${idPrefix}-error`} role="alert" data-testid="auth-error"
             className="mt-4 text-sm text-stamp-red">{error}</p>
        )}
        <button type="button" className="primary-button mt-6 w-full"
                data-testid="auth-forgot-resend"
                disabled={busy || resendLeft > 0}
                onClick={() => void resend()}>
          {resendLeft > 0 ? `重新发送（${resendLeft}s）` : '重新发送'}
        </button>
        <button type="button" className="mt-3 w-full text-xs text-ink-muted transition hover:text-ink"
                onClick={goLogin}>
          返回登录
        </button>
      </div>
    )
  }

  if (mode === 'reset' && resetState !== 'form') {
    const done = resetState === 'done'
    return (
      <div>
        <h2 id={titleId} className="text-xl font-semibold tracking-tight text-balance text-ink">{title}</h2>
        <p className="mt-1.5 text-[13px] text-pretty text-ink-muted">{subtitleText}</p>
        <p role="status" data-testid={done ? 'auth-reset-done' : 'auth-reset-invalid'}
           className={`mt-4 rounded-lg border px-3 py-2 text-xs text-ink ${
             done ? 'border-stamp-green/30 bg-stamp-green/10' : 'border-stamp-amber/40 bg-stamp-amber/10'
           }`}>
          {done
            ? '密码已更新，所有设备已退出登录，请使用新密码重新登录。'
            : '该链接已失效或已被使用，请重新申请一封重置邮件。'}
        </p>
        {done ? (
          <button type="button" className="primary-button mt-6 w-full" onClick={goLogin}>
            返回登录
          </button>
        ) : (
          <>
            <button type="button" className="primary-button mt-6 w-full" onClick={goForgot}>
              重新申请
            </button>
            <button type="button" className="mt-3 w-full text-xs text-ink-muted transition hover:text-ink"
                    onClick={goLogin}>
              返回登录
            </button>
          </>
        )}
      </div>
    )
  }

  return (
    <form onSubmit={(event) => void submit(event)}>
      <h2 id={titleId} className="text-xl font-semibold tracking-tight text-balance text-ink">{title}</h2>
      <p className="mt-1.5 text-[13px] text-pretty text-ink-muted">{subtitleText}</p>

      {notice && (
        <p role="status" data-testid="auth-notice"
           className="mt-4 rounded-lg border border-stamp-green/30 bg-stamp-green/10 px-3 py-2 text-xs text-ink">
          {notice}
        </p>
      )}

      {mode !== 'reset' && (
        <>
          <label className="field-label mt-6" htmlFor={`${idPrefix}-email`}>邮箱</label>
          <input id={`${idPrefix}-email`} name="email" type="email" autoComplete="email"
                 className="field-control" placeholder="you@example.com"
                 aria-invalid={error ? true : undefined}
                 aria-describedby={error ? `${idPrefix}-error` : undefined}
                 value={email} onChange={(event) => setEmail(event.target.value)} required />
        </>
      )}

      {mode !== 'forgot' && (
        <>
          <label className="field-label mt-4" htmlFor={`${idPrefix}-password`}>
            {mode === 'reset' ? '新密码' : '密码'}
          </label>
          <div className="relative">
            <input id={`${idPrefix}-password`} name="password"
                   type={showPassword ? 'text' : 'password'}
                   autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
                   className="field-control pr-12"
                   aria-invalid={error ? true : undefined}
                   aria-describedby={mode === 'reset' ? `${idPrefix}-reset-rules` : undefined}
                   value={password} onChange={(event) => setPassword(event.target.value)}
                   minLength={12} required
                   data-testid={mode === 'reset' ? 'auth-reset-password' : undefined} />
            <button type="button" tabIndex={0}
                    className="absolute inset-y-0 right-0 flex w-12 items-center justify-center rounded-r-lg text-ink-muted transition hover:text-ink"
                    aria-label={showPassword ? '隐藏密码' : '显示密码'}
                    aria-pressed={showPassword}
                    data-testid={mode === 'reset' ? 'auth-reset-password-toggle' : `${idPrefix}-password-toggle`}
                    onClick={() => setShowPassword((visible) => !visible)}>
              {showPassword ? <EyeOffIcon /> : <EyeIcon />}
            </button>
          </div>
          {mode === 'register' && (
            <p className="animate-rise mt-1.5 text-[11px] text-ink-muted">至少 12 位；避免使用常见口令或邮箱账号名</p>
          )}
          {mode === 'reset' && (
            <>
              <ul id={`${idPrefix}-reset-rules`} className="mt-2 space-y-1 text-[11px] text-ink-muted">
                {resetRules.map((rule) => (
                  <li key={rule.label} className="flex items-center gap-1.5">
                    <span aria-hidden="true" className={rule.ok ? 'text-stamp-green' : 'text-ink-muted'}>
                      {rule.ok ? '✓' : '○'}
                    </span>
                    {rule.label}
                  </li>
                ))}
                <li className="flex items-center gap-1.5">
                  <span aria-hidden="true" className="text-ink-muted">○</span>
                  {RESET_RULES_ADVISORY}
                </li>
              </ul>
              <label className="field-label mt-4" htmlFor={`${idPrefix}-reset-confirm`}>确认新密码</label>
              <div className="relative">
                <input id={`${idPrefix}-reset-confirm`} name="confirm"
                       type={showConfirm ? 'text' : 'password'}
                       autoComplete="new-password"
                       className="field-control pr-12"
                       value={confirm} onChange={(event) => setConfirm(event.target.value)}
                       minLength={12} required
                       data-testid="auth-reset-confirm" />
                <button type="button" tabIndex={0}
                        className="absolute inset-y-0 right-0 flex w-12 items-center justify-center rounded-r-lg text-ink-muted transition hover:text-ink"
                        aria-label={showConfirm ? '隐藏密码' : '显示密码'}
                        aria-pressed={showConfirm}
                        data-testid="auth-reset-confirm-toggle"
                        onClick={() => setShowConfirm((visible) => !visible)}>
                  {showConfirm ? <EyeOffIcon /> : <EyeIcon />}
                </button>
              </div>
              {confirm.length > 0 && (
                <p role="status"
                   className={`mt-1.5 text-[11px] ${confirm === password ? 'text-stamp-green' : 'text-stamp-red'}`}>
                  {confirm === password ? '两次输入一致' : '两次输入不一致'}
                </p>
              )}
            </>
          )}
        </>
      )}

      {mode === 'register' && (
        <>
          <label className="field-label animate-rise mt-4" htmlFor={`${idPrefix}-invite`}>邀请码</label>
          <input id={`${idPrefix}-invite`} name="invite_code"
                 className="field-control animate-rise" autoComplete="off" spellCheck={false}
                 style={{ animationDelay: '60ms' }}
                 data-testid={idPrefix === 'invite' ? 'invite-code-input' : undefined}
                 value={inviteCode} onChange={(event) => setInviteCode(event.target.value)} required />
        </>
      )}

      {error && (
        <p id={`${idPrefix}-error`} role="alert" data-testid="auth-error"
           className="mt-4 text-sm text-stamp-red">{error}</p>
      )}

      <button type="submit" className="primary-button mt-6 w-full"
              data-testid={mode === 'forgot' ? 'auth-forgot-submit' : mode === 'reset' ? 'auth-reset-submit' : undefined}
              disabled={busy || (mode === 'reset' && !resetReady)}>
        {busy
          ? mode === 'login'
            ? '登录中…'
            : mode === 'forgot'
              ? '发送中…'
              : mode === 'reset'
                ? '重置中…'
                : '提交中…'
          : mode === 'login'
            ? '登录'
            : mode === 'register'
              ? '注册并登录'
              : mode === 'forgot'
                ? '发送重置链接'
                : '重置密码'}
      </button>

      {mode === 'login' && (
        <button type="button" data-testid="auth-forgot-link"
                className="mt-3 w-full text-xs text-ink-muted transition hover:text-ink"
                onClick={goForgot}>
          忘记密码？
        </button>
      )}
      {(mode === 'forgot' || mode === 'reset') && (
        <button type="button" className="mt-3 w-full text-xs text-ink-muted transition hover:text-ink"
                onClick={goLogin}>
          返回登录
        </button>
      )}
      {(mode === 'login' || mode === 'register') && (
        <button type="button" className="mt-3 w-full text-xs text-ink-muted transition hover:text-ink"
                onClick={() => { setMode(mode === 'login' ? 'register' : 'login'); setError('') }}>
          {mode === 'login' ? '有邀请码？去注册' : '已有账号？去登录'}
        </button>
      )}
      {(mode === 'login' || mode === 'register') && (
        <p className="mt-4 text-center text-[11px] text-ink-muted">
          {mode === 'register' && '注册即表示同意'}
          <button type="button" className="mx-1 underline hover:text-ink"
                  onClick={() => onOpenLegal('terms')}>用户协议</button>
          与
          <button type="button" className="mx-1 underline hover:text-ink"
                  onClick={() => onOpenLegal('privacy')}>隐私政策</button>
        </p>
      )}
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
