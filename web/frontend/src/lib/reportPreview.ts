/** 报告流式预览的状态折叠（需求 35 R4 / TBD-6 协议，2026-10-12）。
 *
 * 协议不变式：
 * 1. 预览 = **最近一次 `REPORT_RESET` 之后**所有 `REPORT_DELTA` 的顺序拼接；
 * 2. 流式开始与半截失败都会发 `REPORT_RESET` ⇒ 预览要么为空、要么是完整的当前轮；
 * 3. 终局以 `RUN_FINISHED.result.report` **整段替换**（本函数不负责终局）。
 *
 * `offset`（本轮累计字符数）只用于连续性诊断：正常按序拼接即可，不参与状态。
 */
import type { AguiEvent } from '../types/agui'

export function applyReportEvent(preview: string, event: AguiEvent): string {
  if (event.type === 'REPORT_DELTA') {
    const text = typeof event.text === 'string' ? event.text : ''
    return text ? preview + text : preview
  }
  if (event.type === 'REPORT_RESET') {
    return ''
  }
  return preview
}
