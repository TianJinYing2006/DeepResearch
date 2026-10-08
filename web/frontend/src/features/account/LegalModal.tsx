/** 法律文本弹窗（验收点③ · 切片 S7）：从 AccountPanel 抽出。
 *
 * 为什么单独成文件：
 * 隐私政策 / 用户协议是**唯一**在三个互斥分支里重复渲染的弹窗
 * （落地页、登录门、已登录账号条），原实现把整块 JSX 存成 `legalModal` 常量复用三次。
 * 抽成组件后三处调用点只留一行，行为不变。
 *
 * ⚠️ 拆分纪律（不可破坏的两条，来自拆分前的实测风险清单）：
 * 1. **三个挂载点必须仍然互斥**。`Modal` 用模块级栈判定栈顶，Escape / 焦点陷阱只由栈顶响应；
 *    若把本组件提到某一层「全局只挂一次」，或让两处同时挂载，就会多 push 一个栈项，
 *    Escape 关错层。因此这里**不做**全局单例，仍由调用方按分支渲染。
 * 2. **弹窗的 overlay 必须仍在其兄弟节点之后渲染**。落地页是 `fixed inset-0 z-50`，
 *    本弹窗 overlay 同为 `z-50`，靠 DOM 顺序取胜；调整挂载顺序会把弹窗盖住，
 *    而 e2e 只断言 `toBeVisible()`，**测不出遮挡**。
 *
 * 另注：`resetLocalData`（登出清理）**刻意不清**本切片的状态 —— `Modal` 对
 * `document.body.style.overflow` 是每实例保存/恢复，父弹窗先卸载、子弹窗后卸载
 * 会把 overflow 永久留在 `'hidden'`。登出时让法律弹窗自然保持即可。
 */
import { useCallback, useState } from 'react'

import { readErrorMessage } from '../../lib/api'
import Modal from '../../components/Modal'
import { ReportView } from '../../components/ReportView'

export type LegalDocKey = 'privacy' | 'terms'

type LegalState = { doc: LegalDocKey; markdown: string }

export type LegalDocController = {
  legal: LegalState | null
  legalError: string
  open: (doc: LegalDocKey) => Promise<void>
  close: () => void
}

/** 取法律文本。行为与原 `openLegal` 逐行等价（含「先占位再填充」的乐观初始态）。 */
export function useLegalDoc(): LegalDocController {
  const [legal, setLegal] = useState<LegalState | null>(null)
  const [legalError, setLegalError] = useState('')

  const close = useCallback(() => {
    setLegal(null)
    setLegalError('')
  }, [])

  const open = useCallback(async (doc: LegalDocKey) => {
    setLegalError('')
    // 先落占位再请求：弹窗立刻出现并显示「加载中…」，而不是点完没反应。
    setLegal({ doc, markdown: '' })
    try {
      const response = await fetch(`/api/legal/${doc}`)
      if (!response.ok) {
        setLegalError(await readErrorMessage(response))
        return
      }
      const body = (await response.json()) as { markdown: string }
      setLegal({ doc, markdown: body.markdown })
    } catch {
      setLegalError('网络错误，请重试')
    }
  }, [])

  return { legal, legalError, open, close }
}

export function LegalModal({ legal, legalError, onClose }: {
  legal: LegalState | null
  legalError: string
  onClose: () => void
}) {
  if (!legal) return null
  return (
    <Modal
      onClose={onClose}
      labelledBy="legal-title"
      testId="legal-modal"
      overlayClassName="z-50 flex items-start justify-center overflow-y-auto bg-ink/35 p-4 "
      panelClassName="surface-card my-6 w-full max-w-3xl p-6"
    >
      <div className="flex items-center justify-between">
        <h3 id="legal-title" className="text-sm font-semibold text-ink">
          {legal.doc === 'privacy' ? '隐私政策' : '用户协议'}
        </h3>
        <button type="button" className="text-xs text-ink-muted hover:text-ink"
                data-testid="legal-close"
                onClick={onClose}>关闭</button>
      </div>
      {legalError && <p role="alert" className="mt-3 text-sm text-stamp-red">{legalError}</p>}
      {!legalError && !legal.markdown && <p className="mt-3 text-sm text-ink-muted">加载中…</p>}
      {!legalError && legal.markdown && (
        <div className="mt-4">
          <ReportView report={legal.markdown} />
        </div>
      )}
    </Modal>
  )
}
