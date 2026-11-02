import { describe, expect, it } from 'vitest'

import { shouldResetChunks } from './reportChunks'

describe('shouldResetChunks（#341 流式展开不被重置）', () => {
  it('流式追加（next 是 previous 的前缀扩展）不重置', () => {
    let report = '# 报告\n\n第一段'
    expect(shouldResetChunks(report, report)).toBe(false)
    for (const next of [
      '# 报告\n\n第一段\n\n第二段',
      '# 报告\n\n第一段\n\n第二段\n\n第三段',
    ]) {
      expect(shouldResetChunks(report, next)).toBe(false)
      report = next
    }
  })

  it('换了一份报告 / 文本缩短（非前缀扩展）时重置', () => {
    expect(shouldResetChunks('旧报告内容', '新报告内容')).toBe(true)
    expect(shouldResetChunks('abcdef', 'abc')).toBe(true)
  })

  it('预览被清空（REPORT_RESET）重置一次，随后从空串增长的增量不重置', () => {
    expect(shouldResetChunks('报告A正文', '')).toBe(true)
    expect(shouldResetChunks('', '报告B正文')).toBe(false)
  })
})
