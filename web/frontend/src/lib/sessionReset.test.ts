import { describe, expect, it } from 'vitest'

import {
  ACTIVE_RUN_KEY,
  clearSignedOutStorage,
  DRAFT_KEY,
  IDLE_RUN_STATE,
  LAST_REQUEST_KEY,
  SIGNED_OUT_STORAGE_KEYS,
} from './sessionReset'

/** 与 `sessionStorage` 同形的最小 fake：只实现本模块用到的 `removeItem`（及测试需要的前置写入）。 */
class FakeStorage {
  private readonly data = new Map<string, string>()

  getItem(key: string): string | null {
    return this.data.get(key) ?? null
  }

  setItem(key: string, value: string): void {
    this.data.set(key, value)
  }

  removeItem(key: string): void {
    this.data.delete(key)
  }
}

/** 造出账号 A 登出前的真实状态：草稿 / 上次请求 / 活跃 run 都在。 */
function staleAccountStorage(): FakeStorage {
  const storage = new FakeStorage()
  storage.setItem(DRAFT_KEY, JSON.stringify({ topic: '账号A的私有主题', instructions: '账号A的要求' }))
  storage.setItem(LAST_REQUEST_KEY, JSON.stringify({ topic: '账号A的私有主题', instructions: '', profile: 'quick' }))
  storage.setItem(ACTIVE_RUN_KEY, 'run-account-a')
  return storage
}

describe('登出清理（#328）', () => {
  it('清除三个会话级键，且不误删同一 storage 里的其它键', () => {
    const storage = staleAccountStorage()
    storage.setItem('dr.unrelated', 'keep-me')

    clearSignedOutStorage(storage)

    expect(SIGNED_OUT_STORAGE_KEYS).toEqual([ACTIVE_RUN_KEY, DRAFT_KEY, LAST_REQUEST_KEY])
    for (const key of SIGNED_OUT_STORAGE_KEYS) {
      expect(storage.getItem(key)).toBeNull()
    }
    expect(storage.getItem('dr.unrelated')).toBe('keep-me')
  })

  it('reset 后运行域为空闲态：runId=null 且三个 sessionStorage 键被移除', () => {
    const storage = staleAccountStorage()

    // 与 useResearchStream.resetRunState 共用同一份空闲态 / 键清单
    clearSignedOutStorage(storage)

    expect(IDLE_RUN_STATE.runId).toBeNull()
    expect(IDLE_RUN_STATE.status).toBe('idle')
    expect(IDLE_RUN_STATE.result).toBeNull()
    expect(IDLE_RUN_STATE.reportPreview).toBe('')
    expect(SIGNED_OUT_STORAGE_KEYS.every((key) => storage.getItem(key) === null)).toBe(true)
  })

  it('storage 缺失 / 删除抛错时静默（隐私模式不阻断登出）', () => {
    expect(() => clearSignedOutStorage(null)).not.toThrow()
    expect(() => clearSignedOutStorage(undefined)).not.toThrow()

    const throwing = {
      removeItem: () => {
        throw new Error('QuotaExceededError')
      },
    }
    expect(() => clearSignedOutStorage(throwing)).not.toThrow()
  })
})
