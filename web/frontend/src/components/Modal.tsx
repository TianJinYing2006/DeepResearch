import { useEffect, useRef, type ReactNode } from 'react'
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

/** 打开中的弹窗栈（后打开的在上层）：Escape / 焦点陷阱只由**最上层**响应，
 *  这是堆叠弹窗（登录门 → 法律文本）下唯一可靠的归属判定。 */
const modalStack: symbol[] = []

/**
 * R1（审计 U5–U7/U15/U48）：统一弹窗底座。
 *
 * - `role="dialog"` + `aria-modal` + `aria-labelledby`；
 * - 打开时锁定背景滚动并聚焦首个可聚焦元素，关闭时归还焦点；
 * - **document 级捕获**的 Tab 焦点陷阱 + focusin 兜底（焦点逃逸会被拉回最上层弹窗）——
 *   不依赖事件从对话框冒泡，避免动画/挂载时序导致的焦点竞态；
 * - 可关闭弹窗支持 Escape 与遮罩点击；遮罩 `overscroll-contain`，避免滚动穿透。
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
  const idRef = useRef<symbol>(Symbol('dr-modal'))
  // onClose 通常是内联箭头函数（每次渲染都变）：用 ref 固定，避免 effect 反复重挂
  const onCloseRef = useRef(onClose)
  useEffect(() => {
    onCloseRef.current = onClose
  }, [onClose])

  useEffect(() => {
    const id = idRef.current
    modalStack.push(id)
    restoreRef.current = document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null

    const dialog = dialogRef.current
    const focusables = (): HTMLElement[] => {
      if (!dialog) return []
      return Array.from(dialog.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
        (element) => !element.hasAttribute('disabled') && element.offsetParent !== null,
      )
    }
    const focusFirst = () => {
      const items = focusables()
      if (items.length > 0) items[0].focus()
      else dialog?.focus()
    }
    focusFirst()

    const isTop = () => modalStack[modalStack.length - 1] === id
    const onKeyDown = (event: globalThis.KeyboardEvent) => {
      if (!isTop()) return
      if (event.key === 'Escape' && dismissible) {
        event.preventDefault()
        onCloseRef.current()
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
      const outside = !dialog?.contains(active)
      if (event.shiftKey && (active === first || active === dialog || outside)) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && (active === last || outside)) {
        event.preventDefault()
        first.focus()
      }
    }
    const onFocusIn = (event: FocusEvent) => {
      if (!isTop()) return
      if (dialog && !dialog.contains(event.target as Node)) focusFirst()
    }
    document.addEventListener('keydown', onKeyDown, true)
    document.addEventListener('focusin', onFocusIn, true)

    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.removeEventListener('keydown', onKeyDown, true)
      document.removeEventListener('focusin', onFocusIn, true)
      const index = modalStack.lastIndexOf(id)
      if (index >= 0) modalStack.splice(index, 1)
      document.body.style.overflow = previousOverflow
      const restore = restoreRef.current
      if (restore && document.contains(restore)) restore.focus()
    }
  }, [dismissible])

  return createPortal(
    <div
      className={`fixed inset-0 overscroll-contain animate-fade-in ${overlayClassName}`}
      data-testid={testId}
      style={{
        // baseline-ui：固定元素尊重刘海/手势区（正常屏幕等价于 p-4）
        paddingTop: 'max(1rem, env(safe-area-inset-top))',
        paddingRight: 'max(1rem, env(safe-area-inset-right))',
        paddingBottom: 'max(1rem, env(safe-area-inset-bottom))',
        paddingLeft: 'max(1rem, env(safe-area-inset-left))',
      }}
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
        className={`animate-scale-in ${panelClassName}`}
      >
        {children}
      </div>
    </div>,
    document.body,
  )
}
