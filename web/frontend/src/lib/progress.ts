/** 进度计算（需求 9 §7.5）。

⚠️ **为什么不做「0→100% 的单一进度条」**：Agent 流程的总节点数**天生不可知** ——
`critic` 条件边决定要不要继续跑，跑多少跳由 LLM 裁决，事前无从得知。
硬做一个 0→100% 的条，结果要么是纯动画糊弄，要么是事后从 0 直接跳到 100。

⇒ 这里**只用能确证的数据**，分两层：

1. **阶段进度**（5 个阶段：规划/检索/撰写/校验/渲染）—— 100% 确定。
2. **检索阶段内的跳数进度**（`depth / max_total_hops`）—— 100% 确定。

只有 **ETA 是估算**（基于实测的节点数中位数），UI 上必须标注「约」。
*/
import type { AguiEvent } from '../types/agui'

export const STAGES = [
  { name: '规划', nodes: ['plan'] },
  { name: '检索', nodes: ['research', 'critic', 'revise'] },
  { name: '撰写', nodes: ['write'] },
  { name: '校验', nodes: ['validate'] },
  { name: '渲染', nodes: ['render'] },
] as const

/** 预估总节点数 —— 实测中位数（62 题，`progress` 条目与 graph 节点 1:1）。
 *  出处：docs/requirements/9-web-ui-rewrite.md §5.4.1。仅用于 ETA，不参与百分比。 */
const EST_TOTAL_NODES = 24

export interface ProgressInput {
  /** 最近完成的节点名 */
  currentNode: string | null
  depth: number
  maxTotalHops: number
  /** 已完成节点数 */
  nodeCount: number
  /** 累计耗时（毫秒） */
  elapsedMs: number
  finished: boolean
}

export interface Progress {
  stageIndex: number // 1..5，0 表示尚未开始
  stageName: string
  percent: number // 0..100
  innerPercent: number // 当前阶段内的进度 0..100
  etaSeconds: number | null
  /** ETA 是否为估算值（恒为 true，提醒 UI 标注「约」） */
  etaIsEstimate: boolean
}

export function stageOf(node: string | null): number {
  if (!node) return -1
  return STAGES.findIndex((s) => (s.nodes as readonly string[]).includes(node))
}

export function computeProgress(input: ProgressInput): Progress {
  const { currentNode, depth, maxTotalHops, nodeCount, elapsedMs, finished } = input
  const idx = stageOf(currentNode)

  if (finished) {
    return {
      stageIndex: STAGES.length,
      stageName: '完成',
      percent: 100,
      innerPercent: 100,
      etaSeconds: 0,
      etaIsEstimate: false,
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
    }
  }

  // 阶段内进度：检索阶段用**真实的**跳数比；其余阶段只有一个节点 ⇒ 完成即 100%
  const isResearch = idx === 1
  const inner = isResearch && maxTotalHops > 0
    ? Math.min(depth / maxTotalHops, 1)
    : 1

  const percent = Math.min(((idx + inner) / STAGES.length) * 100, 99)

  // ETA：平均单节点耗时 × 预估剩余节点数（**估算**）
  const avgNodeMs = elapsedMs / nodeCount
  const remainNodes = Math.max(EST_TOTAL_NODES - nodeCount, 0)
  const etaSeconds = remainNodes === 0 ? null : Math.round((remainNodes * avgNodeMs) / 1000)

  return {
    stageIndex: idx + 1,
    stageName: STAGES[idx].name,
    percent: Math.round(percent),
    innerPercent: Math.round(inner * 100),
    etaSeconds,
    etaIsEstimate: true,
  }
}

/** 从事件流里抽出进度所需的原始量（纯函数，便于单测）。 */
export function summarize(events: AguiEvent[]): ProgressInput {
  let currentNode: string | null = null
  let depth = 0
  let maxTotalHops = 20
  let nodeCount = 0
  let elapsedMs = 0
  let finished = false

  for (const ev of events) {
    if (ev.type === 'RUN_STARTED') {
      const v = Number(ev.max_total_hops)
      if (Number.isFinite(v) && v > 0) maxTotalHops = v
    } else if (ev.type === 'STEP_FINISHED') {
      currentNode = String(ev.node ?? '')
      depth = Number(ev.depth ?? depth)
      elapsedMs += Number(ev.duration_ms ?? 0)
      nodeCount += 1
    } else if (ev.type === 'RUN_FINISHED' || ev.type === 'RUN_ERROR') {
      finished = true
    }
  }
  return { currentNode, depth, maxTotalHops, nodeCount, elapsedMs, finished }
}
