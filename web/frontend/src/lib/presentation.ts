/** 展示逻辑（R4a：从 App.tsx 迁出，纯函数 + 词表，行为不变）。 */
import { formatDuration, formatNumber } from './format'
import type { ConnectionStatus, StreamStatus } from '../hooks/useResearchStream'
import type {
  AguiEvent,
  DegradationEvent,
  RunFinishedEvent,
  StateDeltaEvent,
  StepFinishedEvent,
} from '../types/agui'

export const NODE_LABELS: Record<string, string> = {
  plan: '规划问题',
  research: '检索证据',
  critic: '评估缺口',
  revise: '修订查询',
  write: '撰写报告',
  validate: '校验引用',
  repair: '修复引用',
  render: '渲染结果',
}

export function isStepFinished(event: AguiEvent): event is StepFinishedEvent {
  return event.type === 'STEP_FINISHED'
}

export function isDegradation(event: AguiEvent): event is DegradationEvent {
  return event.type === 'DEGRADATION'
}

export function lastEventOfType(events: AguiEvent[], type: AguiEvent['type']): AguiEvent | undefined {
  for (let index = events.length - 1; index >= 0; index -= 1) {
    if (events[index].type === type) return events[index]
  }
  return undefined
}

export function latestActivity(events: AguiEvent[]): string {
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index]
    if (event.type === 'STATE_DELTA') {
      const added = (event as StateDeltaEvent).progress_added
      const message = added[added.length - 1]?.msg
      if (message) return message
    }
  }
  return ''
}

export function statusPresentation(status: StreamStatus) {
  return {
    idle: { label: '等待任务', className: 'border-rule bg-rule/30 text-ink-muted' },
    starting: { label: '正在启动', className: 'border-stamp-blue/30 bg-stamp-blue/10 text-stamp-blue' },
    running: { label: '研究进行中', className: 'border-stamp-green/30 bg-stamp-green/10 text-stamp-green badge-live' },
    stopping: { label: '正在安全停止', className: 'border-stamp-amber/30 bg-stamp-amber/10 text-stamp-amber' },
    done: { label: '研究完成', className: 'border-stamp-green/30 bg-stamp-green/10 text-stamp-green' },
    cancelled: { label: '已取消', className: 'border-stamp-amber/30 bg-stamp-amber/10 text-stamp-amber' },
    timeout: { label: '已到时限停止', className: 'border-stamp-amber/30 bg-stamp-amber/10 text-stamp-amber' },
    error: { label: '运行失败', className: 'border-stamp-red/30 bg-stamp-red/10 text-rose-200' },
  }[status]
}

export function connectionPresentation(status: ConnectionStatus) {
  return {
    idle: { label: '等待实时流', className: 'border-rule bg-rule/30 text-ink-muted', dotClass: 'bg-ink-muted/50' },
    connecting: { label: '连接中', className: 'border-stamp-blue/30 bg-stamp-blue/10 text-stamp-blue', dotClass: 'animate-pulse bg-stamp-blue' },
    live: { label: '实时连接', className: 'border-stamp-green/30 bg-stamp-green/10 text-stamp-green', dotClass: 'bg-stamp-green' },
    reconnecting: { label: '正在重连', className: 'border-stamp-amber/30 bg-stamp-amber/10 text-stamp-amber', dotClass: 'animate-pulse bg-stamp-amber' },
    closed: { label: '连接已关闭', className: 'border-rule bg-rule/30 text-ink-muted', dotClass: 'bg-ink-muted/50' },
  }[status]
}

export function eventPresentation(event: AguiEvent) {
  switch (event.type) {
    case 'RUN_STARTED':
      return { icon: '▶', iconClass: 'border-stamp-blue/30 bg-stamp-blue/10 text-stamp-blue', title: '研究已启动', detail: `检索上限 ${String(event.max_total_hops ?? '—')} 跳 · 子问题上限 ${String(event.max_subquestions ?? '—')} 个` }
    case 'STEP_FINISHED': {
      const step = event as StepFinishedEvent
      return { icon: '✓', iconClass: 'border-stamp-green/30 bg-stamp-green/10 text-stamp-green', title: nodeLabel(step.node), detail: `${formatDuration(step.duration_ms)} · 深度 ${step.depth} · ${formatNumber(step.token_used)} token` }
    }
    case 'STATE_DELTA': {
      const delta = event as StateDeltaEvent
      const message = delta.progress_added[delta.progress_added.length - 1]?.msg || '状态已更新'
      const governance = delta.planner_events_count > 0
        ? ` · 规划治理 ${delta.planner_events_count} 条`
        : ''
      return { icon: '↗', iconClass: 'border-stamp-blue/30 bg-stamp-blue/10 text-stamp-blue', title: '研究状态更新', detail: message + governance }
    }
    case 'DEGRADATION': {
      const degradation = event as DegradationEvent
      return { icon: '!', iconClass: 'border-stamp-amber/30 bg-stamp-amber/10 text-stamp-amber', title: `${degradation.component} 已降级`, detail: degradation.detail || degradation.reason }
    }
    case 'RUN_FINISHED': {
      const runFinished = event as RunFinishedEvent
      return { icon: '■', iconClass: runFinished.cancelled ? 'border-stamp-amber/30 bg-stamp-amber/10 text-stamp-amber' : 'border-stamp-green/30 bg-stamp-green/10 text-stamp-green', title: runFinished.cancelled ? '研究已在安全边界停止' : '研究已完成', detail: `${formatNumber(runFinished.token_used)} token · ${runFinished.degradation_count} 项降级` }
    }
    case 'RUN_ERROR':
      return { icon: '×', iconClass: 'border-stamp-red/30 bg-rose-300/[0.07] text-rose-200', title: '研究运行失败', detail: String(event.message ?? '未知错误') }
    default:
      return { icon: '·', iconClass: 'border-rule bg-rule/30 text-ink-muted', title: event.type, detail: '事件已接收' }
  }
}

export function nodeLabel(node: string): string {
  return NODE_LABELS[node] ?? node
}

export function isKnowledgeBaseSource(source: string): boolean {
  return source.startsWith('rag:') || source.startsWith('local://')
}

/** 命中来源 → 文件名（`rag:<filename>` / `local://<path>`；去掉分块锚点）。 */
export function knowledgeBaseName(source: string): string {
  const stripped = source.startsWith('rag:')
    ? source.slice(4)
    : source.replace(/^local:\/\//, '')
  const withoutChunk = stripped.split('#')[0]
  return withoutChunk || source
}

export function sourceType(source: string): string {
  if (source.startsWith('rag:') || source.startsWith('local://')) return 'rag'
  if (source.includes('arxiv.org')) return 'arxiv'
  if (source.startsWith('code:')) return 'code'
  return 'web'
}

export function reflectionSummary(entry: Record<string, unknown>): string {
  const summary = entry.gap ?? entry.knowledge_gap ?? entry.reason ?? entry.summary
  if (summary != null && String(summary).trim()) return String(summary)
  const rest = Object.entries(entry)
    .filter(([key]) => !['depth', 'decision'].includes(key))
    .map(([key, value]) => `${humanizeKey(key)}：${formatUnknown(value)}`)
  return rest.join('；') || '未提供更多说明。'
}

export function humanizeKey(key: string): string {
  return key.replace(/_/g, ' ')
}

export function formatUnknown(value: unknown): string {
  if (typeof value === 'number') {
    if (value >= 0 && value <= 1 && !Number.isInteger(value)) return `${Math.round(value * 100)}%`
    return value.toLocaleString('zh-CN')
  }
  if (typeof value === 'boolean') return value ? '是' : '否'
  if (value == null) return '—'
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}
