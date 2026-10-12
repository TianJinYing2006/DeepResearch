/** 需求 35 R4：报告流式预览折叠的单测（TBD-6 协议）。
 *
 * 锁定三条契约：增量按序拼接 / reset 清空 / 其他事件不触碰预览。 */
import { describe, expect, it } from 'vitest'

import { applyReportEvent } from './reportPreview'
import type { AguiEvent } from '../types/agui'

function delta(text: string, offset = text.length): AguiEvent {
  return { type: 'REPORT_DELTA', text, offset }
}

describe('applyReportEvent：报告流式预览折叠', () => {
  it('增量按到达顺序拼接', () => {
    let preview = ''
    preview = applyReportEvent(preview, delta('# 标题'))
    preview = applyReportEvent(preview, delta('正文'))
    expect(preview).toBe('# 标题正文')
  })

  it('reset 清空预览（半截失败协议：reset 后绝不保留半截文本）', () => {
    let preview = applyReportEvent('', delta('半截'))
    preview = applyReportEvent(preview, { type: 'REPORT_RESET' })
    expect(preview).toBe('')
    // reset 之后的新一轮从零开始
    preview = applyReportEvent(preview, delta('新正文'))
    expect(preview).toBe('新正文')
  })

  it('空文本增量与未知事件不改变预览', () => {
    const preview = applyReportEvent('已有', delta(''))
    expect(preview).toBe('已有')
    expect(applyReportEvent('已有', { type: 'RUN_STARTED' })).toBe('已有')
    expect(applyReportEvent('已有', { type: 'STATE_DELTA' })).toBe('已有')
  })

  it('缺 text 字段的畸形增量被忽略（不抛错）', () => {
    expect(applyReportEvent('已有', { type: 'REPORT_DELTA' })).toBe('已有')
  })
})
