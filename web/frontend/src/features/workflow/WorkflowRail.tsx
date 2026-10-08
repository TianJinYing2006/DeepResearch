/** 工作流阶段栏（验收点①）：让用户随时知道「整条链路走到哪、下一步干什么」。
 *
 * 与运行状态徽标的分工：徽标回答「这一次运行现在怎么样」，阶段栏回答「链路走到哪了」。
 * 两者都由同一份状态派生，但关注点不同 —— 因此这里刻意**不重复**运行状态文案。
 *
 * 设计约束（对齐方向 B「研究档案」与 skill 规则）：
 * - 单一强调色：只有「当前阶段」用 `stamp-blue`，已完成用中性色 + 对勾，未开始用淡中性色；
 * - 不用编号标记（设计文档 §1 把「01/02/03」列为模板化 tell）——用对勾表达「走过」，
 *   用实心标记表达「在这」，语义比序号直接；
 * - 无渐变、无光晕；窄屏 `flex-wrap` 换行，不产生横向滚动。
 */
import { Fragment } from 'react'

import type { WorkflowState } from '../../lib/workflow'

function stageClassName(state: 'done' | 'current' | 'pending'): string {
  const base = 'inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] transition'
  if (state === 'current') {
    return `${base} border-stamp-blue/40 bg-stamp-blue/10 font-semibold text-stamp-blue`
  }
  if (state === 'done') {
    return `${base} border-rule bg-rule/30 text-ink-muted hover:text-ink`
  }
  return `${base} border-rule/60 text-ink-muted/70 hover:text-ink-muted`
}

export function WorkflowRail({ state }: { state: WorkflowState }) {
  return (
    <nav
      aria-label="研究流程"
      data-testid="workflow-rail"
      className="surface-card flex flex-wrap items-center gap-x-2 gap-y-2 px-4 py-3"
    >
      {state.stages.map((stage, index) => {
        const stageState = stage.current ? 'current' : stage.done ? 'done' : 'pending'
        // 只有**已到达或当前**的阶段才是链接：未到达的阶段在页面上还没有对应区块
        // （`#stage-report` 由 `{result && <ReportCard/>}` 决定存在与否），
        // 指向不存在锚点的链接是坏门面 —— 点了没反应比不可点更糟。
        const clickable = stage.done || stage.current
        const content = (
          <>
            <span aria-hidden="true">{stage.done ? '✓' : stage.current ? '◆' : '○'}</span>
            {stage.label}
          </>
        )
        return (
          <Fragment key={stage.key}>
            {index > 0 && (
              <span aria-hidden="true" className="text-[11px] text-ink-muted/40">→</span>
            )}
            {clickable ? (
              <a
                href={`#${stage.anchor}`}
                data-testid={`workflow-stage-${stage.key}`}
                data-stage-state={stageState}
                // `aria-current="step"` 是 stepper 的标准语义：读屏会播报「当前步骤」，
                // 而不是把这一项读成普通链接。
                aria-current={stage.current ? 'step' : undefined}
                className={stageClassName(stageState)}
              >
                {content}
              </a>
            ) : (
              <span
                data-testid={`workflow-stage-${stage.key}`}
                data-stage-state={stageState}
                className={stageClassName(stageState)}
              >
                {content}
              </span>
            )}
          </Fragment>
        )
      })}
      {/* 中断时不额外造一个状态：链路的当前阶段已经回落到「运行」，
          这里只补一句人话，说明为什么后面两段没有内容可看。 */}
      {state.interrupted && (
        <span className="text-[11px] text-ink-muted" data-testid="workflow-interrupted">
          本次流程在产出报告前停止
        </span>
      )}
    </nav>
  )
}
