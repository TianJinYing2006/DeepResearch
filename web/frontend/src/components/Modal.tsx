import { useEffect, useRef, type KeyboardEvent, type ReactNode } from 'react'
import { createPortal } from 'react-dom'

type Props = {
  onClose: () => void
  /** 弹窗标题元素 id（role=dialog 的 aria-labelledby 指向它）。 */
  labelledBy: string
  testId: string
  /** false = 不可关闭（如登录门）：无 Escape / 遮罩关闭，仅保留焦点管理。 */
  dismissible?: boolean
  overlayClassName: string
  panelClassName?: string
  children: ReactNode
}

const FOCUSABLE =
  'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'

/**
 * R1（审计 U5–U7/U15/U48）：统一弹窗底座。
 *
 * - `role="dialog"` + `aria-modal` + `aria-labelledby`；
 * - 打开时锁定背景滚动并聚焦首个可聚焦元素，关闭时归还焦点；
 * - Tab/Shift+Tab 循环（焦点陷阱），可关闭弹窗支持 Escape 与遮罩点击；
 * - 遮罩 `overscroll-contain`，避免滚动穿透。
 */
export default function Modal({
  onClose,
  labelledBy,
  testId,
  dismissible = true,
  overlayClassName,
  panelClassName = '',
  children,
}: Props) {
  const dialogRef = useRef<HTMLDivElement>(null)
  const restoreRef = useRef<HTMLElement | null>(null)

  useEffect(() => {
    restoreRef.current = document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null
    const dialog = dialogRef.current
    const first = dialog?.querySelector<HTMLElement>(FOCUSABLE)
    if (first) first.focus()
    else dialog?.focus()

    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.body.style.overflow = previousOverflow
      const restore = restoreRef.current
      if (restore && document.contains(restore)) restore.focus()
    }
  }, [])

  function focusables(): HTMLElement[] {
    const dialog = dialogRef.current
    if (!dialog) return []
    return Array.from(dialog.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
      (element) => !element.hasAttribute('disabled') && element.offsetParent !== null,
    )
  }

  function handleKeyDown(event: KeyboardEvent<HTMLDivElement>): void {
    if (event.key === 'Escape' && dismissible) {
      event.stopPropagation()
      onClose()
      return
    }
    if (event.key !== 'Tab') return
    const items = focusables()
    if (items.length === 0) {
      event.preventDefault()
      return
    }
    const first = items[0]
    const last = items[items.length - 1]
    const active = document.activeElement
    if (event.shiftKey && (active === first || active === dialogRef.current)) {
      event.preventDefault()
      last.focus()
    } else if (!event.shiftKey && active === last) {
      event.preventDefault()
      first.focus()
    }
  }

  return createPortal(
    <div
      className={`fixed inset-0 overscroll-contain ${overlayClassName}`}
      data-testid={testId}
      onMouseDown={(event) => {
        if (dismissible && event.target === event.currentTarget) onClose()
      }}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={labelledBy}
        tabIndex={-1}
        className={panelClassName}
        onKeyDown={handleKeyDown}
      >
        {children}
      </div>
    </div>,
    document.body,
  )
}
