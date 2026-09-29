/** 左栏板块（R4a）：新研究表单 / 运行边界 / 文档摄取状态。 */
import type { FormEvent, KeyboardEvent } from 'react'
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
}

export function LaunchForm({
  topic, instructions, profile, profileOptions, activeProfile, activeProfileIndex,
  running, status, onTopicChange, onInstructionsChange, onProfileChange, onSubmit, onCancel,
}: LaunchFormProps) {
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
    <form className="surface-card p-5" onSubmit={onSubmit}>
      <div className="mb-6 flex items-start justify-between gap-3">
        <div>
          <p className="text-sm font-semibold text-white">新研究</p>
          <p className="mt-1 text-xs leading-5 text-slate-400">描述问题，研究过程会实时推送到右侧。</p>
        </div>
        <span className="rounded-lg border border-emerald-300/15 bg-emerald-300/[0.07] px-2 py-1 text-[10px] font-bold uppercase tracking-[0.16em] text-emerald-200/80">
          Live
        </span>
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

      <label className="field-label mt-5" htmlFor="instructions">附加要求 <span className="normal-case tracking-normal text-slate-400">（可选）</span></label>
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
        className="relative mt-2 flex overflow-hidden rounded-xl border border-white/10 bg-black/20 transition hover:border-white/20"
      >
        {/* 滑动高亮块：与搜索引擎切换器同款交互（300ms ease-out） */}
        <span
          aria-hidden="true"
          className="absolute inset-y-0 left-0 rounded-lg bg-emerald-400/[0.14] ring-1 ring-inset ring-emerald-400/40 transition-transform duration-300 ease-out"
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
                  ? 'text-emerald-200'
                  : 'text-slate-400 hover:text-slate-200'
              } disabled:cursor-not-allowed disabled:text-slate-600 disabled:hover:text-slate-600`}
            >
              {option.label}
            </button>
          ))
        ) : (
          <span className="relative z-10 flex-1 px-3 py-2.5 text-xs text-slate-400">加载中…</span>
        )}
      </div>
      <p className="mt-1.5 text-[10px] leading-4 text-slate-400">
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
        <button className="danger-button mt-3 w-full" type="button" onClick={onCancel} disabled={status === 'stopping'}>
          <span>{status === 'stopping' ? '正在安全停止' : '停止研究'}</span>
        </button>
      )}

      {status === 'stopping' && (
        <p className="mt-3 text-xs leading-5 text-amber-100/70">
          取消请求已生效。当前节点会自然结束，系统不会再启动下一节点。
        </p>
      )}
    </form>
  )
}

export function BoundaryCard() {
  return (
    <section className="surface-card p-5">
      <p className="text-xs font-semibold uppercase tracking-[0.16em] text-slate-400">运行边界</p>
      <div className="mt-4 space-y-3 text-xs leading-5 text-slate-400">
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
          <h2 className="text-sm font-semibold text-white">文档摄取状态</h2>
          <p className="mt-1 text-xs text-slate-400">本轮知识库参与情况</p>
        </div>
        <span className="rounded-full border border-white/10 px-2.5 py-1 font-mono text-xs tabular-nums text-slate-300">{ragSources.length}</span>
      </div>
      {result ? (
        ragSources.length > 0 ? (
          <div className="mt-4">
            <p className="text-xs leading-5 text-slate-400">
              本轮命中 {ragSources.length} 个本地知识库文件：
            </p>
            {/* R4a（审计 U16）：滚动区可聚焦，键盘可滚动查看 */}
            <ul className="mt-2 max-h-40 space-y-1 overflow-y-auto pr-1" data-testid="rag-hit-list"
                tabIndex={0} role="region" aria-label="命中的知识库文件">
              {ragSources.map((source) => (
                <li key={source} title={source} data-testid="rag-hit-item"
                    className="flex items-center gap-2 rounded-lg border border-white/[0.06] bg-black/20 px-2.5 py-1.5 text-xs text-emerald-50/90">
                  <span className="min-w-0 flex-1 truncate">{knowledgeBaseName(source)}</span>
                </li>
              ))}
            </ul>
          </div>
        ) : (
          <p className="mt-4 text-xs leading-5 text-slate-400">本轮结果未命中本地知识库文件。</p>
        )
      ) : (
        <p className="mt-4 text-xs leading-5 text-slate-400">
          研究完成后显示 RAG 文档命中情况；当前协议不伪造摄取进度。
        </p>
      )}
    </section>
  )
}
