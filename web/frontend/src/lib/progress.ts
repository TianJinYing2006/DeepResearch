/** 进度计算（需求 9 §7.5）。

⚠️ **为什么不做「0→100% 的单一进度条」**：Agent 流程的总节点数**天生不可知** ——
`critic` 条件边决定要不要继续跑，跑多少跳由 LLM 裁决，事前无从得知。
硬做一个 0→100% 的条，结果要么是纯动画糊弄，要么是事后从 0 直接跳到 100。

⇒ 这里**只用能确证的数据**，分两层：

1. **阶段进度**（5 个阶段：规划/检索/撰写/校验/渲染）—— 100% 确定。
2. **检索阶段内的跳数进度**（`depth / max_total_hops`）—— 100% 确定。

只有 **ETA 是估算**（基于实测的节点数中位数），UI 上必须标注「约」。
*/
import type { AguiEvent, RunFinishedEvent } from '../types/agui'

export const STAGES = [
  { name: '规划', nodes: ['plan'] },
  { name: '检索', nodes: ['research', 'critic', 'revise'] },
  { name: '撰写', nodes: ['write'] },
  { name: '校验', nodes: ['validate', 'repair'] },
  { name: '渲染', nodes: ['render'] },
] as const

/** 预估总节点数 —— 实测中位数（62 题，`progress` 条目与 graph 节点 1:1）。
 *  出处：docs/requirements/9-web-ui-rewrite.md §5.4.1。仅用于 ETA，不参与百分比。 */
const EST_TOTAL_NODES = 24

/** 终局类型（2026-10-31 #308：失败/取消/超时都不能再显示成「完成 100%」）。 */
export type ProgressOutcome = 'running' | 'finished' | 'cancelled' | 'timeout' | 'error'

/** 进度所需原始量（R5：由事件流增量累加，不再每帧全量重扫）。 */
export interface ProgressSummary {
  /** 最近完成的节点名 */
  currentNode: string | null
  depth: number
  maxTotalHops: number
  /** 已完成节点数 */
  nodeCount: number
  /** 累计耗时（毫秒） */
  elapsedMs: number
  /** 终局类型；running = 仍在执行 */
  outcome: ProgressOutcome
}

export function emptySummary(): ProgressSummary {
  return {
    currentNode: null,
    depth: 0,
    maxTotalHops: 20,
    nodeCount: 0,
    elapsedMs: 0,
    outcome: 'running',
  }
}

/** 单事件增量更新（O(1)）；`summarize` = 事件流上的 reduce。 */
export function applyEvent(summary: ProgressSummary, event: AguiEvent): ProgressSummary {
  if (event.type === 'RUN_STARTED') {
    const value = Number(event.max_total_hops)
    if (Number.isFinite(value) && value > 0) {
      return { ...summary, maxTotalHops: value }
    }
    return summary
  }
  if (event.type === 'STEP_FINISHED') {
    return {
      ...summary,
      currentNode: String(event.node ?? ''),
      depth: Number(event.depth ?? summary.depth),
      elapsedMs: summary.elapsedMs + Number(event.duration_ms ?? 0),
      nodeCount: summary.nodeCount + 1,
    }
  }
  if (event.type === 'RUN_FINISHED') {
    const finished = event as RunFinishedEvent
    // ⚠️ 后端超时收口是 RUN_FINISHED(stop_reason=timeout, cancelled=false)；
    // 只看 cancelled 会把「已到时限停止」误判成正常完成（#308 自审发现）。
    const outcome: ProgressOutcome =
      finished.stop_reason === 'timeout'
        ? 'timeout'
        : finished.cancelled || finished.stop_reason === 'cancelled'
          ? 'cancelled'
          : 'finished'
    return { ...summary, outcome }
  }
  if (event.type === 'RUN_ERROR') {
    return { ...summary, outcome: 'error' }
  }
  return summary
}

export interface Progress {
  stageIndex: number // 1..5，0 表示尚未开始
  stageName: string
  percent: number // 0..100
  innerPercent: number // 当前阶段内的进度 0..100
  etaSeconds: number | null
  /** ETA 是否为估算值（恒为 true，提醒 UI 标注「约」） */
  etaIsEstimate: boolean
  /** 终局类型：finished = 正常完成；error/cancelled/timeout = 已停止（不得显示 100%） */
  outcome: ProgressOutcome
}

function stageOf(node: string | null): number {
  if (!node) return -1
  return STAGES.findIndex((s) => (s.nodes as readonly string[]).includes(node))
}

export function computeProgress(input: ProgressSummary): Progress {
  const { currentNode, depth, maxTotalHops, nodeCount, elapsedMs, outcome } = input
  const idx = stageOf(currentNode)

  // 阶段内进度：检索阶段用**真实的**跳数比；其余阶段只有一个节点 ⇒ 完成即 100%
  const inner = idx === 1 && maxTotalHops > 0
    ? Math.min(depth / maxTotalHops, 1)
    : idx >= 0
      ? 1
      : 0
  const partialPercent = idx < 0 ? 0 : Math.min(((idx + inner) / STAGES.length) * 100, 99)

  if (outcome === 'finished') {
    return {
      stageIndex: STAGES.length,
      stageName: '完成',
      percent: 100,
      innerPercent: 100,
      etaSeconds: 0,
      etaIsEstimate: false,
      outcome,
    }
  }

  // 🚨 #308：失败/取消/超时是「停止」不是「完成」——不得出现 100% / 已完成。
  if (outcome === 'error' || outcome === 'cancelled' || outcome === 'timeout') {
    return {
      stageIndex: idx >= 0 ? idx + 1 : 0,
      stageName:
        outcome === 'error'
          ? '运行失败'
          : outcome === 'timeout'
            ? '已到时限停止'
            : '已停止',
      percent: Math.round(partialPercent),
      innerPercent: Math.round(inner * 100),
      etaSeconds: null,
      etaIsEstimate: false,
      outcome,
    }
  }

  if (idx < 0 || nodeCount === 0) {
    return {
      stageIndex: 0,
      stageName: '准备中',
      percent: 0,
      innerPercent: 0,
      etaSeconds: null,
      etaIsEstimate: true,
      outcome,
    }
  }

  // ETA：平均单节点耗时 × 预估剩余节点数（**估算**）
  const avgNodeMs = elapsedMs / nodeCount
  const remainNodes = Math.max(EST_TOTAL_NODES - nodeCount, 0)
  const etaSeconds = remainNodes === 0 ? null : Math.round((remainNodes * avgNodeMs) / 1000)

  return {
    stageIndex: idx + 1,
    stageName: STAGES[idx].name,
    percent: Math.round(partialPercent),
    innerPercent: Math.round(inner * 100),
    etaSeconds,
    etaIsEstimate: true,
    outcome,
  }
}

/** 从事件流里抽出进度所需的原始量（纯函数，便于单测）。 */
export function summarize(events: AguiEvent[]): ProgressSummary {
  return events.reduce(applyEvent, emptySummary())
}
