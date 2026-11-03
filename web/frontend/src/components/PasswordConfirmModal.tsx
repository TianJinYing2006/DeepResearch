import { useId, useState } from 'react'

import Modal from './Modal'

type Props = {
  /** 弹窗标题（操作名，如「注销账号」）。 */
  title: string
  /** 一行说明（为什么需要密码、会发生什么）。 */
  description?: string
  /** 主按钮文案（默认「确认」）。 */
  confirmLabel?: string
  /** 父级提交中：禁用输入与按钮，避免重复提交。 */
  busy?: boolean
  /** 父级返回的错误文案（就地展示，不关闭弹窗）。 */
  error?: string
  onConfirm: (password: string) => void
  onClose: () => void
}

/**
 * 敏感操作的密码确认弹窗（审计 #336，替代 `window.prompt`）。
 *
 * 为什么必须替换：`window.prompt` 的输入框**不掩码**（明文显示密码，肩窥风险）、
 * 不可本地化/不可样式化，且可能被浏览器抑制——抑制后 Passkey 管理/退出设备/注销
 * 永久不可用。本组件复用统一 `Modal`（dialog 语义 + 焦点陷阱 + Escape + 滚动锁），
 * 密码只保存在组件内，提交后随弹窗卸载，不进入 URL / storage / 日志。
 */
export default function PasswordConfirmModal({
  title,
  description,
  confirmLabel = '确认',
  busy = false,
  error = '',
  onConfirm,
  onClose,
}: Props) {
  const [password, setPassword] = useState('')
  const titleId = useId()
  const inputId = useId()
  const errorId = useId()
  const canSubmit = password.length > 0 && !busy

  return (
    <Modal
      onClose={() => {
        if (!busy) onClose()
      }}
      labelledBy={titleId}
      testId="password-confirm"
      overlayClassName="z-[60] flex items-center justify-center bg-ink/35 p-4"
      panelClassName="surface-card w-full max-w-sm p-6"
    >
      <h3 id={titleId} className="text-sm font-semibold text-ink">{title}</h3>
      {description && <p className="mt-1.5 text-xs leading-5 text-ink-muted">{description}</p>}
      <form
        onSubmit={(event) => {
          event.preventDefault()
          if (canSubmit) onConfirm(password)
        }}
      >
        <label className="field-label mt-4" htmlFor={inputId}>当前密码</label>
        <input
          id={inputId}
          type="password"
          autoComplete="current-password"
          className="field-control"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          disabled={busy}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? errorId : undefined}
          data-testid="password-confirm-input"
        />
        {error && (
          <p id={errorId} role="alert" data-testid="password-confirm-error"
             className="mt-2 text-xs leading-5 text-stamp-red">{error}</p>
        )}
        <div className="mt-4 flex gap-2">
          <button type="button" className="secondary-button flex-1" disabled={busy}
                  data-testid="password-confirm-cancel" onClick={onClose}>
            取消
          </button>
          <button type="submit" className="primary-button flex-1" disabled={!canSubmit}
                  data-testid="password-confirm-submit">
            {busy ? '处理中…' : confirmLabel}
          </button>
        </div>
      </form>
    </Modal>
  )
}
