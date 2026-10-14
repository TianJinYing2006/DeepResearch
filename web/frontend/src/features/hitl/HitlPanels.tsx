/** 需求 36：HITL 交互卡片（澄清 / 计划审批）与报告后追问面板。
 *
 * 与后端 `web/backend/hitl.py` / `followup.py` 的协议对齐：
 * - 卡片提交 `POST /api/research/{run_id}/interaction`（interaction_id 幂等）；
 * - 追问 `POST /api/research/{run_id}/follow-up`（证据池 grounding，不重搜）。
 */
import { useState } from 'react'

import { formatExpiry, isExpired } from '../../lib/hitl'
import type { FollowupEntry, InteractionCard, StructuredError } from '../../types/agui'

type InteractionPanelProps = {
  card: InteractionCard
  busy: boolean
  error: StructuredError | null
  onAnswer: (action: string, text: string) => void
}

export function InteractionPanel({ card, busy, error, onAnswer }: InteractionPanelProps) {
  const [text, setText] = useState('')
  const isClarify = card.kind === 'clarification'
  const questions = card.content.questions ?? []
  const subquestions = card.content.subquestions ?? []
  const allowed = card.allowed_actions ?? []
  const canApprove = allowed.includes('approve')
  const canRevise = allowed.includes('revise')
  const expired = isExpired(card)

  return (
    <section
      className="surface-card border border-stamp-blue/30 p-4 sm:p-5 animate-rise"
      data-testid="hitl-card"
      data-hitl-kind={card.kind}
      aria-live="polite"
    >
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <span
          className="inline-flex rounded-full border border-stamp-blue/40 bg-stamp-blue/10 px-2.5 py-1 text-[11px] font-semibold text-stamp-blue"
          data-testid="hitl-round"
        >
          {isClarify ? '澄清提问' : '计划审批'} · 第 {card.round} 轮
          {card.max_rounds ? ` / 共 ${card.max_rounds} 轮` : ''}
        </span>
        {card.expires_at && (
          <span className="text-[11px] text-ink-muted">
            等待截止 {formatExpiry(card.expires_at)}
          </span>
        )}
        {expired && (
          <span className="text-[11px] font-semibold text-stamp-amber" data-testid="hitl-expired-hint">
            已到等待时限：{isClarify ? '研究将按空回答继续' : '任务将停止（审批不自动通过）'}
          </span>
        )}
      </div>

      {isClarify ? (
        <>
          <p className="mt-3 text-sm text-ink">
            开始研究前需要确认研究范围（回答将作为研究规格的一部分）：
          </p>
          <ul className="mt-2 list-disc space-y-1 pl-5 text-sm leading-6 text-ink" data-testid="hitl-questions">
            {questions.map((question, index) => (
              <li key={index}>{question}</li>
            ))}
          </ul>
          <label className="field-label mt-4" htmlFor="hitl-clarify-input">你的回答</label>
          <textarea
            id="hitl-clarify-input"
            className="field-control min-h-20 resize-y"
            data-testid="hitl-clarify-input"
            value={text}
            onChange={(event) => setText(event.target.value)}
            maxLength={2000}
            placeholder="例如：范围收敛到近三个月，聚焦国内市场"
            disabled={busy}
          />
          <button
            type="button"
            className="primary-button mt-3"
            data-testid="hitl-clarify-submit"
            disabled={busy || !text.trim()}
            onClick={() => {
              const answer = text.trim()
              if (answer) onAnswer('reply', answer)
            }}
          >
            {busy ? '提交中…' : '提交回答'}
          </button>
        </>
      ) : (
        <>
          {card.content.revision_failed && (
            <p className="mt-3 text-xs leading-5 text-stamp-amber" data-testid="hitl-revision-failed">
              上一轮修改未生效（已保留原计划）：可重试修改，或直接批准。
            </p>
          )}
          <p className="mt-3 text-sm text-ink">请检查研究计划（子问题清单）：</p>
          <ol className="mt-2 space-y-2" data-testid="hitl-plan-list">
            {subquestions.map((sub) => (
              <li key={sub.id} className="rounded-md border border-rule px-3 py-2">
                <span className="font-mono text-[11px] text-ink-muted">{sub.id}</span>
                <p className="text-sm leading-6 text-ink">{sub.question}</p>
                {sub.rationale && (
                  <p className="mt-1 text-[11px] leading-5 text-ink-muted">{sub.rationale}</p>
                )}
              </li>
            ))}
          </ol>
          {canRevise && (
            <>
              <label className="field-label mt-4" htmlFor="hitl-plan-revise-input">
                修改意见（自然语言）
              </label>
              <textarea
                id="hitl-plan-revise-input"
                className="field-control min-h-16 resize-y"
                data-testid="hitl-plan-revise-input"
                value={text}
                onChange={(event) => setText(event.target.value)}
                maxLength={2000}
                placeholder="例如：删掉海外市场那条，补充政策风险角度"
                disabled={busy}
              />
            </>
          )}
          <div className="mt-3 flex flex-wrap gap-2">
            {canApprove && (
              <button
                type="button"
                className="primary-button"
                data-testid="hitl-plan-approve"
                disabled={busy}
                onClick={() => onAnswer('approve', '')}
              >
                {busy ? '提交中…' : '批准并开始研究'}
              </button>
            )}
            {canRevise && (
              <button
                type="button"
                className="secondary-button"
                data-testid="hitl-plan-revise"
                disabled={busy || !text.trim()}
                onClick={() => {
                  const feedback = text.trim()
                  if (feedback) onAnswer('revise', feedback)
                }}
              >
                提交修改意见
              </button>
            )}
          </div>
        </>
      )}

      {error && (
        <p className="mt-2 text-xs text-stamp-red" data-testid="hitl-error">
          {error.message}
        </p>
      )}
    </section>
  )
}

type FollowupPanelProps = {
  busy: boolean
  error: StructuredError | null
  entries: FollowupEntry[]
  onAsk: (question: string) => void
}

export function FollowupPanel({ busy, error, entries, onAsk }: FollowupPanelProps) {
  const [question, setQuestion] = useState('')

  return (
    <section className="surface-card p-4 sm:p-5" data-testid="followup-panel">
      <h3 className="text-sm font-semibold text-ink">基于已有证据追问</h3>
      <p className="mt-1 text-xs leading-5 text-ink-muted">
        只依据本次报告与证据清单回答，<strong>不重新检索</strong>；引用 ID 由服务端逐字校验，
        池外引用会如实标注为未核实。
      </p>
      <div className="mt-3 flex flex-col gap-2 sm:flex-row">
        <input
          className="field-control flex-1"
          data-testid="followup-input"
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          maxLength={500}
          placeholder="例如：这些结论的证据分别来自哪里？"
          disabled={busy}
          aria-label="追问内容"
        />
        <button
          type="button"
          className="primary-button"
          data-testid="followup-ask"
          disabled={busy || !question.trim()}
          onClick={() => {
            const asked = question.trim()
            if (!asked) return
            onAsk(asked)
            setQuestion('')
          }}
        >
          {busy ? '思考中…' : '追问'}
        </button>
      </div>
      {error && (
        <p className="mt-2 text-xs text-stamp-red" data-testid="followup-error">
          {error.message}
        </p>
      )}
      {entries.length > 0 && (
        <div className="mt-4 space-y-4">
          {entries.map((entry, index) => (
            <div key={index} className="rounded-lg border border-rule p-3" data-testid="followup-entry">
              <p className="text-sm font-medium text-ink">{entry.question}</p>
              <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-ink" data-testid="followup-answer">
                {entry.answer}
              </p>
              {entry.citations.length > 0 && (
                <p className="mt-2 text-[11px] text-ink-muted">
                  引用证据：{entry.citations.join('、')}
                </p>
              )}
              {entry.unverified_ids.length > 0 && (
                <p className="mt-1 text-[11px] text-stamp-amber" data-testid="followup-unverified">
                  未能核实的引用：{entry.unverified_ids.join('、')}
                </p>
              )}
              {!entry.grounded && entry.citations.length === 0 && (
                <p className="mt-1 text-[11px] text-stamp-amber">
                  该回答未引用证据，请谨慎参考。
                </p>
              )}
            </div>
          ))}
        </div>
      )}
    </section>
  )
}
