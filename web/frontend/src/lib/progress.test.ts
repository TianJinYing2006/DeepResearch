import { describe, expect, it } from 'vitest'
import type { AguiEvent } from '../types/agui'
import { applyEvent, computeProgress, emptySummary, summarize } from './progress'

/** 事件构造器：测试只关心 type 与个别字段，其余由 AguiEvent 的索引签名兜底。 */
function ev(type: string, extra: Record<string, unknown> = {}): AguiEvent {
  return { type, ...extra } as AguiEvent
}

function step(node: string, index: number, depth = 0): AguiEvent {
  return ev('STEP_FINISHED', { node, index, duration_ms: 1200, depth, token_used: 100 })
}

describe('progress.computeProgress（#308 失败/取消不得显示完成）', () => {
  it('RUN_ERROR：显示运行失败，percent < 100 且不点亮全部阶段', () => {
    const progress = computeProgress(
      summarize([
        ev('RUN_STARTED', { max_total_hops: 20 }),
        step('plan', 1),
        ev('RUN_ERROR', { message: 'Error code: 429 - limit_requests' }),
      ]),
    )
    expect(progress.outcome).toBe('error')
    expect(progress.stageName).toBe('运行失败')
    expect(progress.percent).toBeLessThan(100)
    expect(progress.stageIndex).toBeLessThan(5)
    expect(progress.etaSeconds).toBeNull()
  })

  it('RUN_ERROR 且尚无 STEP_FINISHED：0%、阶段 0，不显示完成', () => {
    const progress = computeProgress(
      summarize([ev('RUN_STARTED', { max_total_hops: 20 }), ev('RUN_ERROR', { message: 'boom' })]),
    )
    expect(progress.outcome).toBe('error')
    expect(progress.stageName).toBe('运行失败')
    expect(progress.percent).toBe(0)
    expect(progress.stageIndex).toBe(0)
  })

  it('RUN_FINISHED(cancelled)：显示已停止，percent < 100', () => {
    const progress = computeProgress(
      summarize([
        ev('RUN_STARTED', { max_total_hops: 20 }),
        step('research', 1, 3),
        ev('RUN_FINISHED', { cancelled: true, stop_reason: 'cancelled' }),
      ]),
    )
    expect(progress.outcome).toBe('cancelled')
    expect(progress.stageName).toBe('已停止')
    expect(progress.percent).toBeLessThan(100)
  })

  it('RUN_FINISHED(completed)：正常完成才是 100% / 完成', () => {
    const progress = computeProgress(
      summarize([
        ev('RUN_STARTED', { max_total_hops: 20 }),
        step('render', 8),
        ev('RUN_FINISHED', { cancelled: false, stop_reason: 'completed' }),
      ]),
    )
    expect(progress.outcome).toBe('finished')
    expect(progress.stageName).toBe('完成')
    expect(progress.percent).toBe(100)
    expect(progress.stageIndex).toBe(5)
  })

  it('applyEvent：RUN_ERROR 置 error，RUN_FINISHED 区分取消/完成', () => {
    const running = emptySummary()
    expect(running.outcome).toBe('running')
    expect(applyEvent(running, ev('RUN_ERROR')).outcome).toBe('error')
    expect(
      applyEvent(running, ev('RUN_FINISHED', { cancelled: true })).outcome,
    ).toBe('cancelled')
    expect(
      applyEvent(running, ev('RUN_FINISHED', { cancelled: false })).outcome,
    ).toBe('finished')
  })
})
