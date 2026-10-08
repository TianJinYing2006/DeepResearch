/** 工作流阶段模型的单测（验收点①）。
 *
 * 重心：**任何输入下都必须恰好有一个「当前阶段」**，且中断场景不得把用户指向
 * 一个并不存在的报告。这两条是阶段栏可信的全部前提。 */
import { describe, expect, it } from 'vitest'

import { workflowStages } from './workflow'
import type { StreamStatus } from '../hooks/useResearchStream'

function run(status: StreamStatus, hasResult = false, qualityKind: Parameters<typeof workflowStages>[0]['qualityKind'] = null) {
  return workflowStages({ status, hasResult, qualityKind })
}

describe('workflowStages：阶段推进', () => {
  it('未开始 ⇒ 当前是「新建研究」，其余未完成', () => {
    const state = run('idle')
    expect(state.stages.map((s) => s.key)).toEqual(['compose', 'run', 'report', 'verify', 'export'])
    expect(state.stages.find((s) => s.current)?.key).toBe('compose')
    expect(state.stages.every((s) => !s.done)).toBe(true)
    expect(state.interrupted).toBe(false)
  })

  it('运行中 ⇒ 「新建」已完成、当前是「运行」', () => {
    for (const status of ['starting', 'running', 'stopping'] as StreamStatus[]) {
      const state = run(status)
      expect(state.stages.find((s) => s.key === 'compose')?.done, status).toBe(true)
      expect(state.stages.find((s) => s.current)?.key, status).toBe('run')
    }
  })

  it('跑完且有报告但质量未定 ⇒ 当前是「引用核查」', () => {
    const state = run('done', true, 'pending')
    expect(state.stages.find((s) => s.key === 'report')?.done).toBe(true)
    expect(state.stages.find((s) => s.current)?.key).toBe('verify')
    expect(state.interrupted).toBe(false)
  })

  it('质量结论已产生 ⇒ 四段全部完成，且**没有**当前阶段', () => {
    for (const kind of ['passed', 'refuted', 'incomplete', 'relaxed', 'no-citations'] as const) {
      const state = run('done', true, kind)
      expect(state.stages.every((s) => s.done), kind).toBe(true)
      // 全部走完就不该再高亮任何一段 —— 高亮一个已完成的阶段会被读成「还没走完」
      expect(state.stages.some((s) => s.current), kind).toBe(false)
    }
  })

  it('取消 / 超时 / 失败且无报告 ⇒ interrupted，且**不高亮**任何阶段', () => {
    // 刻意不回落到「运行」或「报告」：回落会把一个已完成/不存在的阶段标成「当前」。
    // 「链路断在哪」由 interrupted 的文案表达。
    for (const status of ['cancelled', 'timeout', 'error'] as StreamStatus[]) {
      const state = run(status)
      expect(state.interrupted, status).toBe(true)
      expect(state.stages.some((s) => s.current), status).toBe(false)
      expect(state.stages.find((s) => s.key === 'report')?.done, status).toBe(false)
    }
  })

  it('中断但已有部分结果 ⇒ 不算 interrupted（报告确实存在）', () => {
    const state = run('cancelled', true, 'incomplete')
    expect(state.interrupted).toBe(false)
    expect(state.stages.find((s) => s.key === 'verify')?.done).toBe(true)
  })
})

describe('workflowStages：不变式', () => {
  const STATUSES: StreamStatus[] = [
    'idle', 'starting', 'running', 'stopping', 'done', 'cancelled', 'timeout', 'error',
  ]

  it('任何状态 × 有无报告 × 质量任意 ⇒ 当前阶段至多一个，且必未完成', () => {
    for (const status of STATUSES) {
      for (const hasResult of [false, true]) {
        for (const kind of [null, 'pending', 'passed', 'incomplete'] as const) {
          const state = run(status, hasResult, kind)
          const currents = state.stages.filter((s) => s.current)
          const at = `${status}/${hasResult}/${kind}`
          expect(currents.length, at).toBeLessThanOrEqual(1)
          // 互斥不变式：「当前」不得同时是「已完成」
          if (currents.length === 1) {
            expect(currents[0].done, `${at} 当前阶段不应已完成`).toBe(false)
          }
        }
      }
    }
  })

  it('「已完成」不会出现在「当前阶段」之后（阶段是单调推进的）', () => {
    // 只枚举**可达**组合：`result` 只可能由终局的 RUN_FINISHED 带出，
    // 因此「有报告 + 非终局」在 SSE 协议下不可达（模型对它是自洽的，见 workflow.ts 的蕴含注释）。
    const TERMINAL: StreamStatus[] = ['done', 'cancelled', 'timeout', 'error']
    for (const status of STATUSES) {
      for (const hasResult of [false, true]) {
        if (hasResult && !TERMINAL.includes(status)) continue
        const state = run(status, hasResult, hasResult ? 'passed' : null)
        const currentIndex = state.stages.findIndex((s) => s.current)
        // `currentIndex === -1` = 全部走完、没有当前阶段（合法终态）⇒ 没有「之后」可断言。
        if (currentIndex === -1) continue
        state.stages.forEach((stage, index) => {
          if (index > currentIndex) {
            expect(stage.done, `${status} 第 ${index} 段在当前位置之后却已完成`).toBe(false)
          }
        })
      }
    }
  })

  it('锚点是稳定的 DOM id（供阶段栏跳转）', () => {
    const state = run('idle')
    expect(state.stages.map((s) => s.anchor)).toEqual([
      'stage-compose', 'stage-run', 'stage-report', 'stage-verify', 'stage-export',
    ])
  })
})


describe('workflowStages：导出与分享段（验收点① 的第五步）', () => {
  const base = { status: 'done' as StreamStatus, hasResult: true, qualityKind: 'passed' as const }

  it('报告存在且未命中预检 ⇒ 该段完成', () => {
    const state = workflowStages({ ...base, outputUnderReview: false })
    expect(state.stages.find((s) => s.key === 'export')?.done).toBe(true)
    // 全部完成后不应再高亮任何一段
    expect(state.stages.some((s) => s.current)).toBe(false)
  })

  it('报告命中内容安全预检 ⇒ 该段**未完成**（导出/分享按钮此时是禁用的）', () => {
    // 这条是该段不能与「报告阅读」合并的理由：合并后它永远与 report 同步，等于装饰。
    const state = workflowStages({ ...base, outputUnderReview: true })
    expect(state.stages.find((s) => s.key === 'export')?.done).toBe(false)
    // 当前阶段回落到导出段——用户该去处理的就是「复核/申诉」
    expect(state.stages.find((s) => s.current)?.key).toBe('export')
  })

  it('未传 outputUnderReview 时按 false 处理（向后兼容）', () => {
    const state = workflowStages(base)
    expect(state.stages.find((s) => s.key === 'export')?.done).toBe(true)
  })
})