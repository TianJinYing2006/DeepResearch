/** ReportView 分段展开的「是否重置」判定（审计 #341 / F7）。
 *
 * 背景：旧实现 `useEffect(() => setChunks(1), [report])` 在流式预览下，每次
 * `REPORT_DELTA` 改变 `report` 字符串都会把用户点开的「继续渲染剩余 N 字符」
 * 打回第一段。判定口径：
 *
 * - 同一份文本（含首次挂载 `previous === next`）⇒ 不重置；
 * - `next` 是 `previous` 的前缀扩展（流式追加、同报告变长）⇒ 不重置；
 * - 其余（换了一份报告 / 预览被清空 / 文本缩短）⇒ 重置。
 */
export function shouldResetChunks(previous: string, next: string): boolean {
  if (next === previous) return false
  if (next.startsWith(previous)) return false
  return true
}
