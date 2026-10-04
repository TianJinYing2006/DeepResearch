import { useState } from 'react'
import { csrfHeaders, readErrorMessage } from '../lib/api'
import Modal from './Modal'

type Props = { onClose: () => void }

/** 需求 25：站内产品反馈（3 字段内；成功/失败反馈；页面上下文自动附带）。 */
export default function FeedbackModal({ onClose }: Props) {
  const [category, setCategory] = useState('bug')
  const [message, setMessage] = useState('')
  const [contact, setContact] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [done, setDone] = useState(false)

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setBusy(true)
    setError('')
    try {
      const response = await fetch('/api/feedback', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...csrfHeaders() },
        body: JSON.stringify({
          category,
          message,
          contact: contact.trim() || null,
          page: 'workbench',
        }),
      })
      if (!response.ok) {
        setError(await readErrorMessage(response))
        return
      }
      setDone(true)
    } catch {
      setError('网络错误，请重试')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal
      onClose={onClose}
      labelledBy="feedback-title"
      testId="feedback-modal"
      overlayClassName="z-50 flex justify-center overflow-y-auto bg-ink/35 p-4"
      panelClassName="surface-card my-6 w-full max-w-lg p-6"
    >
      <div className="flex items-center justify-between">
        <h3 id="feedback-title" className="text-sm font-semibold text-ink">提交反馈</h3>
        <button type="button" className="text-xs text-ink-muted hover:text-ink"
                data-testid="feedback-close" onClick={onClose}>关闭</button>
      </div>

      {done ? (
        <div className="mt-4">
          <p role="status" data-testid="feedback-done"
             className="rounded-lg border border-stamp-green/30 bg-stamp-green/10 px-3 py-2 text-xs text-ink">
            已收到你的反馈，我们会尽快跟进；如需回复请留下联系方式。
          </p>
          <button type="button" className="primary-button mt-4 w-full" onClick={onClose}>
            关闭
          </button>
        </div>
      ) : (
        <form onSubmit={(event) => void submit(event)}>
          <label className="field-label mt-4" htmlFor="feedback-category">类型</label>
          <select id="feedback-category" className="field-control" data-testid="feedback-category"
                  value={category} onChange={(event) => setCategory(event.target.value)}>
            <option value="bug">问题反馈（Bug）</option>
            <option value="idea">功能建议</option>
            <option value="other">其他</option>
          </select>

          <label className="field-label mt-4" htmlFor="feedback-message">描述</label>
          <textarea id="feedback-message" className="field-control min-h-24" required
                    minLength={10} maxLength={2000} data-testid="feedback-message"
                    placeholder="发生了什么 / 期望是什么（至少 10 个字）"
                    value={message} onChange={(event) => setMessage(event.target.value)} />

          <label className="field-label mt-4" htmlFor="feedback-contact">联系方式（可选）</label>
          <input id="feedback-contact" className="field-control" maxLength={200}
                 data-testid="feedback-contact" placeholder="邮箱 / 其他"
                 value={contact} onChange={(event) => setContact(event.target.value)} />

          {error && (
            <p role="alert" data-testid="feedback-error"
               className="mt-4 text-sm text-stamp-red">{error}</p>
          )}

          <button type="submit" className="primary-button mt-5 w-full"
                  data-testid="feedback-submit"
                  disabled={busy || message.trim().length < 10}>
            {busy ? '提交中…' : '提交反馈'}
          </button>
        </form>
      )}
    </Modal>
  )
}
