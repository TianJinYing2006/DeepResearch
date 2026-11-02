import { describe, expect, it } from 'vitest'

import { isSessionEpochEvent, notifySessionChanged, SESSION_EPOCH_KEY } from './sessionBroadcast'

class FakeStorage {
  readonly data = new Map<string, string>()

  setItem(key: string, value: string): void {
    this.data.set(key, value)
  }
}

describe('sessionBroadcast（#341b 跨标签页登出联动）', () => {
  it('写会话变更标记：固定 key + 时间戳值，不写用户信息', () => {
    const storage = new FakeStorage()
    notifySessionChanged(storage, 1720000000000)
    expect(storage.data.get(SESSION_EPOCH_KEY)).toBe('1720000000000')
    expect(storage.data.size).toBe(1)
  })

  it('storage 缺失或写入失败时静默（隐私模式降级为不联动）', () => {
    expect(() => notifySessionChanged(null)).not.toThrow()
    expect(() => notifySessionChanged(undefined)).not.toThrow()
    expect(() => notifySessionChanged({
      setItem: () => {
        throw new Error('QuotaExceededError')
      },
    })).not.toThrow()
  })

  it('只处理会话变更 key 的 storage 事件', () => {
    expect(isSessionEpochEvent(SESSION_EPOCH_KEY)).toBe(true)
    expect(isSessionEpochEvent('dr.draft')).toBe(false)
    expect(isSessionEpochEvent(null)).toBe(false)
  })
})
