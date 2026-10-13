/** 需求 36：HITL 交互卡片纯逻辑单测（与后端 hitl.py 的卡片 schema 对齐）。 */
import { describe, expect, it } from 'vitest'

import {
  applyInteractionEvent,
  cardFromEvent,
  formatExpiry,
  interactionFromSnapshot,
  isExpired,
  isInteractionEvent,
} from './hitl'
import type { AguiEvent, InteractionCard } from '../types/agui'

function card(overrides: Partial<InteractionCard> = {}): InteractionCard {
  return {
    interaction_id: 'itx-1',
    kind: 'clarification',
    round: 1,
    content: { questions: ['范围？'] },
    ...overrides,
  }
}

describe('cardFromEvent', () => {
  it('从 CLARIFY_REQUESTED 事件还原卡片', () => {
    const event = {
      type: 'CLARIFY_REQUESTED',
      interaction_id: 'itx-9',
      kind: 'clarification',
      round: 2,
      max_rounds: 2,
      expires_at: '2026-10-14T00:00:00+00:00',
      allowed_actions: ['reply'],
      content: { questions: ['a', 'b'] },
    } as AguiEvent
    expect(cardFromEvent(event)).toEqual({
      interaction_id: 'itx-9',
      kind: 'clarification',
      round: 2,
      max_rounds: 2,
      expires_at: '2026-10-14T00:00:00+00:00',
      allowed_actions: ['reply'],
      content: { questions: ['a', 'b'] },
    })
  })

  it('缺 interaction_id / 非法 kind ⇒ null', () => {
    expect(cardFromEvent({ type: 'PLAN_PROPOSED', kind: 'plan_approval' } as AguiEvent)).toBeNull()
    expect(
      cardFromEvent({ type: 'PLAN_PROPOSED', interaction_id: 'x', kind: 'other' } as AguiEvent),
    ).toBeNull()
  })
})

describe('applyInteractionEvent', () => {
  it('卡片事件设置当前卡片；回答事件按同 id 清除', () => {
    const current = applyInteractionEvent(null, {
      type: 'PLAN_PROPOSED',
      interaction_id: 'itx-p',
      kind: 'plan_approval',
      round: 1,
      content: { subquestions: [{ id: 'q1', question: 'Q' }] },
    } as AguiEvent)
    expect(current?.interaction_id).toBe('itx-p')

    const afterAnswer = applyInteractionEvent(current, {
      type: 'PLAN_DECISION',
      interaction_id: 'itx-p',
    } as AguiEvent)
    expect(afterAnswer).toBeNull()
  })

  it('旧卡清理事件不误清新一轮卡片（id 不一致时保留）', () => {
    const current = card({ interaction_id: 'itx-new', round: 2 })
    const next = applyInteractionEvent(current, {
      type: 'CLARIFY_ANSWERED',
      interaction_id: 'itx-old',
    } as AguiEvent)
    expect(next?.interaction_id).toBe('itx-new')
  })

  it('过期事件清除同 id 卡片；无关事件不动', () => {
    expect(
      applyInteractionEvent(card(), {
        type: 'PLAN_EXPIRED',
        interaction_id: 'itx-1',
      } as AguiEvent),
    ).toBeNull()
    expect(
      applyInteractionEvent(card(), { type: 'STEP_FINISHED' } as AguiEvent)?.interaction_id,
    ).toBe('itx-1')
  })
})

describe('interactionFromSnapshot', () => {
  it('优先用快照里的卡片原件', () => {
    const snapshotCard = card({ interaction_id: 'itx-card' })
    expect(
      interactionFromSnapshot({
        interaction_id: 'itx-card',
        kind: 'clarification',
        round: 1,
        card: snapshotCard,
      }),
    ).toEqual(snapshotCard)
  })

  it('无卡片原件时用协议字段兜底；空视图 ⇒ null', () => {
    const fallback = interactionFromSnapshot({
      interaction_id: 'itx-x',
      kind: 'plan_approval',
      round: 2,
      expires_at: '2026-10-14T00:00:00+00:00',
    })
    expect(fallback).toEqual({
      interaction_id: 'itx-x',
      kind: 'plan_approval',
      round: 2,
      expires_at: '2026-10-14T00:00:00+00:00',
      content: {},
    })
    expect(interactionFromSnapshot(null)).toBeNull()
    expect(interactionFromSnapshot(undefined)).toBeNull()
  })
})

describe('isExpired / formatExpiry / isInteractionEvent', () => {
  it('到期判定与展示', () => {
    const now = Date.parse('2026-10-14T12:00:00+00:00')
    expect(isExpired(card({ expires_at: '2026-10-14T11:59:00+00:00' }), now)).toBe(true)
    expect(isExpired(card({ expires_at: '2026-10-14T12:01:00+00:00' }), now)).toBe(false)
    expect(isExpired(card(), now)).toBe(false)
    expect(formatExpiry('not-a-date')).toBe('not-a-date')
    expect(formatExpiry('2026-10-14T00:00:00+00:00')).toMatch(/10月14日/)
  })

  it('交互事件集合覆盖卡片/回答/过期/追问', () => {
    for (const type of [
      'CLARIFY_REQUESTED',
      'PLAN_PROPOSED',
      'CLARIFY_ANSWERED',
      'PLAN_DECISION',
      'CLARIFY_EXPIRED',
      'PLAN_EXPIRED',
      'FOLLOWUP_ASKED',
      'FOLLOWUP_ANSWERED',
    ]) {
      expect(isInteractionEvent({ type } as AguiEvent)).toBe(true)
    }
    expect(isInteractionEvent({ type: 'STEP_FINISHED' } as AguiEvent)).toBe(false)
  })
})
