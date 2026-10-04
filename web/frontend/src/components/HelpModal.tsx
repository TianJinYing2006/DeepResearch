import { useEffect, useState } from 'react'
import Modal from './Modal'
import { ReportView } from './ReportView'

type Props = { onClose: () => void }

/** 需求 25：帮助中心（FAQ）弹窗；内容来自 `/api/help/faq`（仓库文档唯一来源）。 */
export default function HelpModal({ onClose }: Props) {
  const [markdown, setMarkdown] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    let cancelled = false
    fetch('/api/help/faq')
      .then(async (response) => {
        if (!response.ok) {
          const body = await response.json().catch(() => null)
          const message = (body?.detail?.message as string | undefined) ?? '帮助文档加载失败'
          if (!cancelled) setError(message)
          return
        }
        const data = (await response.json()) as { markdown?: string }
        if (!cancelled) setMarkdown(data.markdown ?? '')
      })
      .catch(() => {
        if (!cancelled) setError('网络错误，请重试')
      })
    return () => { cancelled = true }
  }, [])

  return (
    <Modal
      onClose={onClose}
      labelledBy="help-title"
      testId="help-modal"
      overlayClassName="z-50 flex justify-center overflow-y-auto bg-ink/35 p-4"
      panelClassName="surface-card my-6 w-full max-w-3xl p-6"
    >
      <div className="flex items-center justify-between">
        <h3 id="help-title" className="text-sm font-semibold text-ink">帮助中心</h3>
        <button type="button" className="text-xs text-ink-muted hover:text-ink"
                data-testid="help-close" onClick={onClose}>关闭</button>
      </div>
      {error && <p role="alert" data-testid="help-error" className="mt-3 text-sm text-stamp-red">{error}</p>}
      {!error && !markdown && <p className="mt-3 text-sm text-ink-muted">加载中…</p>}
      {!error && markdown && (
        <div className="mt-4">
          <ReportView report={markdown} />
        </div>
      )}
    </Modal>
  )
}
