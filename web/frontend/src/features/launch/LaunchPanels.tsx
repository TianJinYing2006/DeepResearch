/** 左栏板块（R4a）：新研究表单 / 运行边界 / 文档摄取状态。 */
import { useState, type FormEvent, type KeyboardEvent } from 'react'
import { BoundaryItem } from '../../components/ui'
import type { StreamStatus } from '../../hooks/useResearchStream'
import { knowledgeBaseName } from '../../lib/presentation'
import type { ProfileOption, ResearchResult } from '../../types/agui'

type LaunchFormProps = {
  topic: string
  instructions: string
  profile: string
  profileOptions: ProfileOption[]
  activeProfile: ProfileOption | undefined
  activeProfileIndex: number
  running: boolean
  status: StreamStatus
  onTopicChange: (value: string) => void
  onInstructionsChange: (value: string) => void
  onProfileChange: (value: string) => void
  onSubmit: (event: FormEvent<HTMLFormElement>) => void
  onCancel: () => void
  /** 方向 B：报告完成后自动折叠为摘要条，把首屏让给报告；可手动展开/收起。 */
  collapsed: boolean
  onToggle: () => void
  /** 需求 18（#74）：终局后一键开启新话题（清空输入 + 展开 + 聚焦；档位保留）。 */
  onNewTopic: () => void
}

export function LaunchForm({
  topic, instructions, profile, profileOptions, activeProfile, activeProfileIndex,
  running, status, onTopicChange, onInstructionsChange, onProfileChange, onSubmit, onCancel,
  collapsed, onToggle, onNewTopic,
}: LaunchFormProps) {
  // R4b（审计 U37）：停止为不可逆操作 —— 两步确认，避免误点丢失长任务剩余工作
  const [confirmingStop, setConfirmingStop] = useState(false)

  if (collapsed) {
    return (
      <section id="stage-compose" className="surface-card flex items-center justify-between gap-3 p-4">
        <div className="min-w-0">
          <p className="truncate text-sm font-medium text-ink">{topic.trim() || '新研究'}</p>
          <p className="mt-0.5 text-xs text-ink-muted">
            {activeProfile?.label ?? '默认档位'} · 参数已折叠，优先阅读报告
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <button type="button" className="secondary-button !px-3 !py-2"
                  data-testid="launch-expand" onClick={onToggle}>
            修改参数
          </button>
          {/* 需求 18（#74）：一键开启新话题；运行中禁用（避免清空正在跑的输入） */}
          <button type="button" className="primary-button !px-3 !py-2"
                  data-testid="launch-new-topic" onClick={onNewTopic} disabled={running}>
            新研究
          </button>
        </div>
      </section>
    )
  }

  // R4a（审计 U17）：radiogroup 键盘语义（方向键/Home/End + roving tabindex）
  function handleProfileKey(event: KeyboardEvent<HTMLDivElement>) {
    if (running || profileOptions.length === 0) return
    const current = profileOptions.findIndex((option) => option.value === profile)
    const last = profileOptions.length - 1
    let next = current < 0 ? 0 : current
    if (event.key === 'ArrowRight' || event.key === 'ArrowDown') next = current < 0 ? 0 : (current + 1) % profileOptions.length
    else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') next = current < 0 ? last : (current - 1 + profileOptions.length) % profileOptions.length
    else if (event.key === 'Home') next = 0
    else if (event.key === 'End') next = last
    else return
    event.preventDefault()
    onProfileChange(profileOptions[next].value)
    const buttons = event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="radio"]')
    buttons[next]?.focus()
  }

  const tabbableProfile = activeProfile?.value ?? profileOptions[0]?.value

  return (
    <form id="stage-compose" className="surface-card p-5" onSubmit={onSubmit}>
      <div className="mb-6 flex items-start justify-between gap-3">
        <div>
          <p className="text-sm font-semibold text-ink">新研究</p>
          <p className="mt-1 text-xs leading-5 text-ink-muted">描述问题，研究过程会实时推送到右侧。</p>
        </div>
        <div className="flex items-center gap-2">
          <span className="rounded-lg border border-stamp-blue/20 bg-stamp-blue/10 px-2 py-1 text-[10px] font-bold tracking-[0.16em] text-stamp-blue">
            Live
          </span>
          <button type="button" className="text-xs text-ink-muted underline hover:text-ink"
                  data-testid="launch-collapse" onClick={onToggle}>收起</button>
        </div>
      </div>

      <label className="field-label" htmlFor="topic">研究主题</label>
      <textarea
        id="topic"
        className="field-control min-h-28 resize-y"
        value={topic}
        onChange={(event) => onTopicChange(event.target.value)}
        placeholder="例如：生成式 AI 对企业知识管理的实际影响"
        disabled={running}
        maxLength={1000}
        required
      />

      <label className="field-label mt-5" htmlFor="instructions">附加要求 <span className="normal-case tracking-normal text-ink-muted">（可选）</span></label>
      <textarea
        id="instructions"
        className="field-control min-h-24 resize-y"
        value={instructions}
        onChange={(event) => onInstructionsChange(event.target.value)}
        placeholder="指定时间范围、关注维度、报告风格等"
        disabled={running}
        maxLength={2000}
      />

      <p className="field-label mt-5 mb-0" id="profile-label">运行档位</p>
      <div
        role="radiogroup"
        aria-labelledby="profile-label"
        onKeyDown={handleProfileKey}
        className="relative mt-2 flex overflow-hidden rounded-lg border border-rule bg-rule/40 transition hover:border-ink-muted/50"
      >
        {/* 滑动高亮块：与搜索引擎切换器同款交互（300ms ease-out） */}
        <span
          aria-hidden="true"
          className="absolute inset-y-0 left-0 rounded-lg bg-stamp-blue/10 ring-1 ring-inset ring-stamp-blue/40 transition-transform duration-300 ease-out"
          style={{
            width: `${100 / Math.max(profileOptions.length, 1)}%`,
            transform: `translateX(${activeProfileIndex * 100}%)`,
          }}
        />
        {profileOptions.length > 0 ? (
          profileOptions.map((option) => (
            <button
              key={option.value}
              type="button"
              role="radio"
              aria-checked={profile === option.value}
              tabIndex={option.value === tabbableProfile ? 0 : -1}
              onClick={() => onProfileChange(option.value)}
              disabled={running}
              className={`relative z-10 flex-1 px-3 py-2.5 text-xs font-semibold transition-colors duration-200 ${
                profile === option.value
                  ? 'text-stamp-green'
                  : 'text-ink-muted hover:text-ink'
              } disabled:cursor-not-allowed disabled:text-ink-muted/60 disabled:hover:text-ink-muted/60`}
            >
              {option.label}
            </button>
          ))
        ) : (
          <span className="relative z-10 flex-1 px-3 py-2.5 text-xs text-ink-muted">加载中…</span>
        )}
      </div>
      <p className="mt-1.5 text-[10px] leading-4 text-ink-muted">
        档位由服务端固定底层参数（跳数 / 子问题 / 预算 / 时限），客户端不可覆盖。
        {activeProfile
          ? `当前：≤${activeProfile.max_total_hops} 跳 · ≤${activeProfile.max_subquestions} 个子问题 · ${Math.round(activeProfile.timeout_seconds / 60)} 分钟`
          : ''}
      </p>

      <button className="primary-button mt-6 w-full" type="submit" disabled={running || !topic.trim()}>
        <span>{status === 'starting' ? '启动中' : '开始研究'}</span>
        <span aria-hidden="true">→</span>
      </button>

      {(status === 'running' || status === 'stopping') && (
        confirmingStop && status === 'running' ? (
          <div className="mt-3 flex gap-2">
            <button className="danger-button flex-1" type="button" data-testid="stop-confirm"
                    onClick={() => { setConfirmingStop(false); onCancel() }}>
              确认停止
            </button>
            <button className="secondary-button flex-1" type="button" data-testid="stop-resume"
                    onClick={() => setConfirmingStop(false)}>
              继续研究
            </button>
          </div>
        ) : (
          <button className="danger-button mt-3 w-full" type="button" data-testid="stop-button"
                  onClick={() => setConfirmingStop(true)} disabled={status === 'stopping'}>
            <span>{status === 'stopping' ? '正在安全停止' : '停止研究'}</span>
          </button>
        )
      )}

      {status === 'stopping' && (
        <p className="mt-3 text-xs leading-5 text-stamp-amber">
          取消请求已生效。当前节点会自然结束，系统不会再启动下一节点。
        </p>
      )}
    </form>
  )
}

export function BoundaryCard() {
  return (
    <section className="surface-card p-5">
      <p className="text-xs font-medium text-ink-muted">运行边界</p>
      <div className="mt-4 space-y-3 text-xs leading-5 text-ink-muted">
        <BoundaryItem title="核心逻辑" text="研究判断全部留在 Python 后端" />
        <BoundaryItem title="费用口径" text="只展示后端实值，缺失时不做估算" />
        <BoundaryItem title="取消语义" text="节点边界停止，不把取消记为故障" />
      </div>
    </section>
  )
}

type RagStatusCardProps = {
  result: ResearchResult | null
  ragSources: string[]
}

export function RagStatusCard({ result, ragSources }: RagStatusCardProps) {
  return (
    <section className="surface-card p-5">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold text-ink">文档摄取状态</h2>
          <p className="mt-1 text-xs text-ink-muted">本轮知识库参与情况</p>
        </div>
        <span className="rounded-full border border-rule px-2.5 py-1 font-mono text-xs tabular-nums text-ink">{ragSources.length}</span>
      </div>
      {result ? (
        ragSources.length > 0 ? (
          <div className="mt-4">
            <p className="text-xs leading-5 text-ink-muted">
              本轮命中 {ragSources.length} 个本地知识库文件：
            </p>
            {/* R4a（审计 U16）：滚动区可聚焦，键盘可滚动查看 */}
            <ul className="mt-2 max-h-40 space-y-1 overflow-y-auto pr-1" data-testid="rag-hit-list"
                tabIndex={0} role="region" aria-label="命中的知识库文件">
              {ragSources.map((source) => (
                <li key={source} title={source} data-testid="rag-hit-item"
                    className="flex items-center gap-2 rounded-lg border border-rule bg-rule/40 px-2.5 py-1.5 text-xs text-ink">
                  <span className="min-w-0 flex-1 truncate">{knowledgeBaseName(source)}</span>
                </li>
              ))}
            </ul>
          </div>
        ) : (
          <p className="mt-4 text-xs leading-5 text-ink-muted">本轮结果未命中本地知识库文件。</p>
        )
      ) : (
        <p className="mt-4 text-xs leading-5 text-ink-muted">
          研究完成后显示 RAG 文档命中情况；当前协议不伪造摄取进度。
        </p>
      )}
    </section>
  )
}
