/** 工作流阶段模型（验收点①）：把「我现在在哪一步」变成一个可测的纯函数。
 *
 * 为什么需要它：
 * 页面原先是**一条主列把事情全堆上** —— 新建表单、运行横带、报告、决策轨迹、活动流，
 * 谁是当前该看的东西只能靠用户自己猜。产品的主线其实是一条固定链路：
 *
 *     新建研究 → 等待与进度 → 报告阅读 → 引用核查 → 导出/分享
 *
 * 本模块把这四个**有锚点的阶段**算出来，交给阶段栏呈现与跳转。
 *
 * 为什么是四段而不是五段：用户口径里的「导出/分享」在本页**没有独立区块** ——
 * 它是报告卡片头部的一排按钮，与「报告阅读」同处 `#stage-report`。
 * 为它单列一段会得到一个指向同一锚点的重复项；阶段栏宁可少一段，也不要造一个假的落点。
 * 导出/分享的可达性由「报告阶段已完成」表达（报告未产出时导出按钮本就是禁用的）。
 *
 * 与状态徽标的分工（不重复）：
 * - 状态徽标回答「这一次运行现在怎么样」（含取消/超时/失败）；
 * - 阶段栏回答「整条链路走到哪了、下一步该干什么」。
 * 两者都从同一份状态派生，但**关注点不同**，因此允许看起来相似而不合并。
 */
import type { StreamStatus } from '../hooks/useResearchStream'
import type { QualityKind } from './quality'

export type WorkflowStageKey = 'compose' | 'run' | 'report' | 'verify'

export type WorkflowStage = {
  key: WorkflowStageKey
  label: string
  /** 该阶段已经走完。 */
  done: boolean
  /** 当前所处阶段。**与 `done` 互斥** —— 已走完的阶段不再「当前」。 */
  current: boolean
  /** 该阶段内容的 DOM 锚点（不含 `#`）。 */
  anchor: string
}

export type WorkflowState = {
  stages: WorkflowStage[]
  /** 流程在产出报告前就停了（取消 / 超时 / 失败）。用来诚实标注「停在运行阶段」，
   *  而不是让「报告阅读」显示成当前阶段 —— 那会暗示用户去读一份并不存在的报告。 */
  interrupted: boolean
}

const STAGE_META: Array<{ key: WorkflowStageKey; label: string; anchor: string }> = [
  { key: 'compose', label: '新建研究', anchor: 'stage-compose' },
  { key: 'run', label: '等待与进度', anchor: 'stage-run' },
  { key: 'report', label: '报告阅读', anchor: 'stage-report' },
  { key: 'verify', label: '引用核查', anchor: 'stage-verify' },
]

/** 质量结论是否已经产生（`pending` 与空值都算未产生）。 */
function qualityResolved(kind: QualityKind | null | undefined): boolean {
  return kind != null && kind !== 'pending'
}

export function workflowStages(input: {
  status: StreamStatus
  hasResult: boolean
  qualityKind: QualityKind | null
}): WorkflowState {
  const { status, hasResult, qualityKind } = input
  const started = status !== 'idle'
  const terminal =
    status === 'done' || status === 'cancelled' || status === 'timeout' || status === 'error'

  // 「已有报告」**蕴含**前面的步骤必然发生过 —— 用蕴含关系，而不是靠调用方保证顺序。
  // 这样模型对任意输入都自洽：不会出现「报告已完成、但新建研究未完成」这类阶段倒挂
  // （实测过一次：不加蕴含时 `hasResult=true` 而状态非终局会让末段先于当前段变成已完成）。
  const done: Record<WorkflowStageKey, boolean> = {
    compose: started || hasResult,
    run: terminal || hasResult,
    report: hasResult,
    verify: hasResult && qualityResolved(qualityKind),
  }

  const interrupted = terminal && !hasResult

  // `current` 取**第一个未完成**的阶段；没有未完成阶段（链路全部走完）时为 null。
  //
  // ⚠️ 两条刻意选择：
  // 1. `current` 与 `done` **互斥**。曾经写成「全部完成时 current 停在末段」，
  //    结果末段同时是 done 和 current —— 组件按 current 渲染，界面就把一个已完成的阶段
  //    高亮成「你在这」，读起来像流程还没走完（e2e 实测抓到：期望 done、实得 current）。
  // 2. 中断且无报告时 `current` 也是 null，**不回落**到「报告阅读」。回落会指向一份
  //    不存在的报告；「链路断在哪」由 `interrupted` 的文案表达，不由高亮表达。
  const current: WorkflowStageKey | null = interrupted
    ? null
    : STAGE_META.find((stage) => !done[stage.key])?.key ?? null

  return {
    stages: STAGE_META.map((meta) => ({
      ...meta,
      done: done[meta.key],
      current: meta.key === current,
    })),
    interrupted,
  }
}
