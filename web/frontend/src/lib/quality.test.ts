/** 质量判定模型的单测（验收点②）。
 *
 * 重心：证明**「跑完」与「通过」不会被混为一谈** —— 这是本模型存在的唯一理由，
 * 也是需求 28 那个真实形态（重型题 112 条引用全部未校验，运行照样走到 done）的防复发断言。
 */
import { describe, expect, it } from 'vitest'

import { citationVerdict, isTaskComplete, qualityVerdict } from './quality'
import type { CitationResult, ResearchResult, RunFinishedEvent } from '../types/agui'

function citation(over: Partial<CitationResult> = {}): CitationResult {
  return {
    claim: 'claim',
    source: 'https://example.com/a',
    verified: false,
    supported: false,
    finding_id: 'f1',
    source_type: 'web',
    confidence: 0.5,
    note: '',
    existence: true,
    verified_relaxed: false,
    is_meta: false,
    ...over,
  }
}

function result(citations: CitationResult[]): ResearchResult {
  return {
    report: '# 报告',
    citations,
    validator_stats: {},
    depth: 3,
    visited_sources: [],
    reflection_log: [],
  }
}

function finished(over: Partial<RunFinishedEvent> = {}): RunFinishedEvent {
  return {
    type: 'RUN_FINISHED',
    cancelled: false,
    stop_reason: 'completed',
    run_status: 'success',
    token_used: 1000,
    cost_estimate_cny: 0.5,
    degradation_count: 0,
    has_report: true,
    result: result([]),
    ...over,
  }
}

describe('citationVerdict：四态判定', () => {
  it('verification_failed 优先于 verified —— UNKNOWN 不得降格成通过', () => {
    // 后端可能同时给出 verified=true 与 verification_failed=true（结论错位形态）；
    // 若先判 verified，一条「没查清」的引用会被读成「查过且通过」。
    expect(citationVerdict(citation({ verified: true, verification_failed: true }))).toBe('unknown')
  })

  it('verification_failed 优先于 verified_relaxed', () => {
    expect(
      citationVerdict(citation({ verified_relaxed: true, verification_failed: true })),
    ).toBe('unknown')
  })

  it('严格通过 / 宽松通过 / 确证失败 各自独立', () => {
    expect(citationVerdict(citation({ verified: true }))).toBe('verified')
    expect(citationVerdict(citation({ verified_relaxed: true }))).toBe('relaxed')
    expect(citationVerdict(citation())).toBe('refuted')
  })

  it('verification_failed 缺省（undefined，旧后端）按 false 处理', () => {
    expect(citationVerdict(citation({ verified: true, verification_failed: undefined }))).toBe('verified')
  })
})

describe('isTaskComplete：任务维度只看是否正常跑完', () => {
  it('completed 且未取消 ⇒ 跑完', () => {
    expect(isTaskComplete(finished())).toBe(true)
  })

  it('取消 / 超时 ⇒ 没跑完（与质量高低无关）', () => {
    expect(isTaskComplete(finished({ cancelled: true, stop_reason: 'cancelled' }))).toBe(false)
    expect(isTaskComplete(finished({ stop_reason: 'timeout' }))).toBe(false)
  })

  it('无终局事件 ⇒ 没跑完', () => {
    expect(isTaskComplete(undefined)).toBe(false)
  })
})

describe('qualityVerdict：质量维度', () => {
  it('输出待审核压过一切 —— 此时不评价引用质量', () => {
    const verdict = qualityVerdict(
      result([citation({ verified: true })]),
      finished({ output_under_review: true }),
    )
    expect(verdict.kind).toBe('under-review')
    expect(verdict.label).toBe('输出待审核')
  })

  it('无结果载荷 ⇒ 尚未校验', () => {
    expect(qualityVerdict(undefined, finished()).kind).toBe('pending')
    expect(qualityVerdict(null, undefined).kind).toBe('pending')
  })

  it('零引用 ⇒ 无引用可核（不是「通过」）', () => {
    const verdict = qualityVerdict(result([]), finished())
    expect(verdict.kind).toBe('no-citations')
    expect(verdict.label).toBe('无引用可核')
  })

  it('全部严格通过 ⇒ 引用通过', () => {
    const verdict = qualityVerdict(
      result([citation({ verified: true }), citation({ verified: true })]),
      finished(),
    )
    expect(verdict.kind).toBe('passed')
    expect(verdict.label).toBe('引用通过')
    expect(verdict.tone).toBe('ok')
    expect(verdict.counts).toMatchObject({ total: 2, verified: 2, refuted: 0, unknown: 0 })
  })

  it('无确证失败、无未完成，但严格口径未全过 ⇒ 宽松通过', () => {
    const verdict = qualityVerdict(
      result([citation({ verified: true }), citation({ verified_relaxed: true })]),
      finished(),
    )
    expect(verdict.kind).toBe('relaxed')
    expect(verdict.counts).toMatchObject({ total: 2, verified: 1, relaxed: 1 })
  })

  it('需求 28 真实形态：112 条全部未校验 ⇒ 校验未完成，而不是「完成」', () => {
    const citations = Array.from({ length: 112 }, (_, i) =>
      citation({ finding_id: `f${i}`, verification_failed: true }),
    )
    const verdict = qualityVerdict(result(citations), finished())

    expect(verdict.kind).toBe('incomplete')
    expect(verdict.label).toBe('校验未完成')
    expect(verdict.counts).toMatchObject({ total: 112, unknown: 112, verified: 0, refuted: 0 })
    // 关键断言：任务跑完了，但质量**没有**通过 —— 两个维度必须能同时成立
    expect(verdict.taskComplete).toBe(true)
    expect(verdict.kind).not.toBe('passed')
    expect(verdict.headline).toContain('112')
  })

  it('确证失败优先于校验未完成（已查实的坏消息先于未知）', () => {
    const verdict = qualityVerdict(
      result([citation(), citation({ verification_failed: true })]),
      finished(),
    )
    expect(verdict.kind).toBe('refuted')
    // 但未知的计数不能丢 —— 界面仍要能看到「另有 N 条未完成」
    expect(verdict.counts).toMatchObject({ refuted: 1, unknown: 1 })
    expect(verdict.detail).toContain('1 条校验未完成')
  })

  it('取消的运行也可以有质量结论，但任务维度是未跑完', () => {
    const verdict = qualityVerdict(
      result([citation({ verified: true })]),
      finished({ cancelled: true, stop_reason: 'cancelled' }),
    )
    expect(verdict.kind).toBe('passed')
    expect(verdict.taskComplete).toBe(false)
  })

  it('存在性分母单独统计（严格口径的分母不是总数）', () => {
    const verdict = qualityVerdict(
      result([
        citation({ existence: true, verified: true }),
        citation({ existence: false }),
        citation({ existence: true, verified_relaxed: true }),
      ]),
      finished(),
    )
    expect(verdict.counts).toMatchObject({ total: 3, existence: 2, verified: 1, relaxed: 1, refuted: 1 })
  })
})
