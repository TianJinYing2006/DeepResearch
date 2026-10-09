/** 工作流阶段模型（验收点①）：把「我现在在哪一步」变成一个可测的纯函数。
 *
 * 为什么需要它：
 * 页面原先是**一条主列把事情全堆上** —— 新建表单、运行横带、报告、决策轨迹、活动流，
 * 谁是当前该看的东西只能靠用户自己猜。产品的主线其实是一条固定链路：
 *
 *     新建研究 → 等待与进度 → 报告阅读 → 引用核查 → 导出/分享
 *
 * 本模块计算五个阶段。导出/分享指向报告头部按钮的独立锚点，
 * 报告待审核时，该阶段保持未完成。
 *
 * 与状态徽标的分工（不重复）：
 * - 状态徽标回答「这一次运行现在怎么样」（含取消/超时/失败）；
 * - 阶段栏回答「整条链路走到哪了、下一步该干什么」。
 * 两者都从同一份状态派生，但**关注点不同**，因此允许看起来相似而不合并。
 */
import type { StreamStatus } from '../hooks/useResearchStream'
import type { QualityKind } from './quality'

export type WorkflowStageKey = 'compose' | 'run' | 'report' | 'verify' | 'export'

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
  { key: 'export', label: '导出与分享', anchor: 'stage-export' },
]

/** 质量结论是否已经产生（`pending` 与空值都算未产生）。 */
function qualityResolved(kind: QualityKind | null | undefined): boolean {
  return kind != null && kind !== 'pending'
}

export function workflowStages(input: {
  status: StreamStatus
  /** 报告实际存在（含待审核报告），不能仅用结果对象的存在性判断。 */
  hasResult: boolean
  qualityKind: QualityKind | null
  /** 报告命中内容安全预检时为 true —— 此时正文已脱敏、**导出与分享入口全部禁用**，
   *  因此它是「导出与分享」这一段的真实完成条件，而非可有可无的附加信息。
   *  可选是为了向后兼容：未传按 false 处理（等价于「未命中预检」）。 */
  outputUnderReview?: boolean
}): WorkflowState {
  const { status, hasResult, qualityKind, outputUnderReview = false } = input
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
    // 与 report 不同：报告存在但命中内容安全预检时，导出/分享按钮是禁用的 ⇒ 该段未完成。
    // 这正是它不能与 report 合并的理由（合并后这一段永远与 report 同步，等于装饰）。
    export: hasResult && !outputUnderReview,
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
