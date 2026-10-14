/** 需求 36：HITL 交互协议的前端纯逻辑（可单测，无 React 依赖）。
 *
 * - `applyInteractionEvent`：从事件流重建「当前交互卡片」（SSE 断线重连回放同样适用）；
 * - `interactionFromSnapshot`：页面刷新后从快照 `interaction` 字段恢复卡片；
 * - `formatExpiry` / `isExpired`：卡片到期时间的展示与提示（服务端才是权威判定）。
 */
import type { AguiEvent, InteractionCard, InteractionView } from '../types/agui'

const CARD_EVENT_TYPES = ['CLARIFY_REQUESTED', 'PLAN_PROPOSED'] as const
const CLEAR_EVENT_TYPES = [
  'CLARIFY_ANSWERED',
  'PLAN_DECISION',
  'CLARIFY_EXPIRED',
  'PLAN_EXPIRED',
] as const

export function isInteractionCardEvent(event: AguiEvent): boolean {
  return (CARD_EVENT_TYPES as readonly string[]).includes(event.type)
}

export function isInteractionEvent(event: AguiEvent): boolean {
  return (
    isInteractionCardEvent(event) ||
    (CLEAR_EVENT_TYPES as readonly string[]).includes(event.type) ||
    event.type === 'FOLLOWUP_ASKED' ||
    event.type === 'FOLLOWUP_ANSWERED'
  )
}

export function cardFromEvent(event: AguiEvent): InteractionCard | null {
  const interactionId = typeof event.interaction_id === 'string' ? event.interaction_id : ''
  const kind = event.kind
  if (!interactionId || (kind !== 'clarification' && kind !== 'plan_approval')) return null
  return {
    interaction_id: interactionId,
    kind,
    round: typeof event.round === 'number' ? event.round : 1,
    max_rounds: typeof event.max_rounds === 'number' ? event.max_rounds : undefined,
    expires_at: typeof event.expires_at === 'string' ? event.expires_at : undefined,
    allowed_actions: Array.isArray(event.allowed_actions)
      ? (event.allowed_actions as string[])
      : undefined,
    content:
      event.content && typeof event.content === 'object'
        ? (event.content as InteractionCard['content'])
        : {},
  }
}

/** 事件流 → 当前卡片（null = 无待答交互）。
 *
 * 清理语义：只清「同一张卡」——回答/过期事件携带 `interaction_id`，
 * 与当前卡片不一致时保留（防回放里的旧卡清理事件误清新一轮卡片）。 */
export function applyInteractionEvent(
  current: InteractionCard | null,
  event: AguiEvent,
): InteractionCard | null {
  if (isInteractionCardEvent(event)) return cardFromEvent(event)
  if ((CLEAR_EVENT_TYPES as readonly string[]).includes(event.type)) {
    const id = typeof event.interaction_id === 'string' ? event.interaction_id : ''
    if (!current || !id || id === current.interaction_id) return null
    return current
  }
  return current
}

export function interactionFromSnapshot(view?: InteractionView | null): InteractionCard | null {
  if (!view || !view.interaction_id) return null
  const card = view.card
  if (card && card.interaction_id) return card
  return {
    interaction_id: view.interaction_id,
    kind: view.kind,
    round: view.round ?? 1,
    expires_at: view.expires_at ?? undefined,
    content: {},
  }
}

export function isExpired(card: InteractionCard, now = Date.now()): boolean {
  if (!card.expires_at) return false
  const ts = Date.parse(card.expires_at)
  return Number.isFinite(ts) && ts <= now
}

export function formatExpiry(iso: string): string {
  const ts = Date.parse(iso)
  if (!Number.isFinite(ts)) return iso
  const date = new Date(ts)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${date.getMonth() + 1}月${date.getDate()}日 ${pad(date.getHours())}:${pad(date.getMinutes())}`
}
