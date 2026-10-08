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
import { useCallback, useRef, useState } from 'react'

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

/** 取法律文本。行为与原 `openLegal` 等价（含「先占位再填充」的乐观初始态），
 *  并补上一道**代次守卫**。
 *
 * ⚠️ **代次守卫不可去掉**（本会话实测到的缺陷与修法，有确定性复现用例）：
 * 原实现是「``await fetch`` 之后再无条件 ``setLegal(...)``」。若用户在请求在途时关闭弹窗，
 * 那次写入会把弹窗**重新打开** —— 表现为「按了 Escape、弹窗关了一下，然后又回来」。
 * 在 CI 上更隐蔽：``toHaveCount(0)`` 的轮询间隔约 100ms，会**错过中间计数为 0 的瞬间**，
 * 于是断言一直看到 1 而失败（本会话 #160 的 e2e 偶发失败即此 —— 重跑通过、本机无法复现）。
 *
 * 守卫方式：``close()`` 递增代次使在途请求失效；响应回来时若代次已变则丢弃。
 * 用代次而不是 ``cancelled`` 布尔量，是因为「关掉 A 又立刻打开 B」时后者也必须胜出。 */
export function useLegalDoc(): LegalDocController {
  const [legal, setLegal] = useState<LegalState | null>(null)
  const [legalError, setLegalError] = useState('')
  const generationRef = useRef(0)

  const close = useCallback(() => {
    // 递增代次 ⇒ 任何在途请求的响应到达时都会发现自己已过期
    generationRef.current += 1
    setLegal(null)
    setLegalError('')
  }, [])

  const open = useCallback(async (doc: LegalDocKey) => {
    const generation = generationRef.current + 1
    generationRef.current = generation
    setLegalError('')
    // 先落占位再请求：弹窗立刻出现并显示「加载中…」，而不是点完没反应。
    setLegal({ doc, markdown: '' })
    try {
      const response = await fetch(`/api/legal/${doc}`)
      if (generation !== generationRef.current) return  // 已被关闭或被更晚的 open 取代 ⇒ 丢弃
      if (!response.ok) {
        setLegalError(await readErrorMessage(response))
        return
      }
      const body = (await response.json()) as { markdown: string }
      if (generation !== generationRef.current) return  // json() 也是异步的，再查一次
      setLegal({ doc, markdown: body.markdown })
    } catch {
      if (generation !== generationRef.current) return
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
