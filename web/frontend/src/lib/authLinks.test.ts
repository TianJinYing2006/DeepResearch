import { describe, expect, it } from 'vitest'

import { extractResetToken } from './authLinks'

describe('extractResetToken（#341b 重置 token 解析）', () => {
  it('提取合法 token（10~256 位 urlsafe）', () => {
    expect(extractResetToken('#reset=good-token-1234567890')).toBe('good-token-1234567890')
    expect(extractResetToken(`#reset=${'a'.repeat(256)}`)).toBe('a'.repeat(256))
  })

  it('非法 / 非重置 hash 一律返回 null', () => {
    expect(extractResetToken('')).toBeNull()
    expect(extractResetToken('#reset=short')).toBeNull()
    expect(extractResetToken(`#reset=${'a'.repeat(257)}`)).toBeNull()
    expect(extractResetToken('#reset=bad token!')).toBeNull()
    expect(extractResetToken('#other=good-token-1234567890')).toBeNull()
    expect(extractResetToken('#reset=good-token-1234567890&x=1')).toBeNull()
  })
})
