import { describe, expect, it } from 'vitest'

import { createRequestGuard } from './requestGuard'

describe('createRequestGuard（#341 历史请求竞态守卫）', () => {
  it('旧 token 在 next() 之后失效，最新 token 有效', () => {
    const guard = createRequestGuard()
    const first = guard.next()
    expect(guard.isCurrent(first)).toBe(true)

    const second = guard.next()
    expect(guard.isCurrent(first)).toBe(false)
    expect(guard.isCurrent(second)).toBe(true)
    expect(second).toBeGreaterThan(first)
  })

  it('invalidate 使所有在途 token 失效（卸载/外部重置不发起新请求）', () => {
    const guard = createRequestGuard()
    const token = guard.next()
    guard.invalidate()
    expect(guard.isCurrent(token)).toBe(false)
  })

  it('不同 guard 互不影响（多面板并发）', () => {
    const panelA = createRequestGuard()
    const panelB = createRequestGuard()
    const tokenA = panelA.next()
    panelB.next()
    expect(panelA.isCurrent(tokenA)).toBe(true)
  })
})
