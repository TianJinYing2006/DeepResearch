/** 展示格式化（R2）：数字 / 金额 / 时长 / 时间 / 字节 —— 全站唯一实现。
 *
 * 行为与搬迁前保持一致（时长统一为 `2m 13s` 短格式；金额保留分级精度），
 * 仅把散落在 App / AccountPanel / ProgressBar 的重复实现收敛到这里。
 */

export function formatNumber(value: number): string {
  return Number.isFinite(value) ? value.toLocaleString('zh-CN') : '0'
}

export function formatCost(cny: number | null | undefined): string {
  // R1（审计 U2）：畸形/缺失终局帧不得让渲染崩溃 —— 非有限值显示占位
  if (typeof cny !== 'number' || !Number.isFinite(cny)) return '—'
  // 研究单跑常在几分钱量级，固定两位会把 0.004 显示成 0.00 ⇒ 小额度多留两位
  if (cny === 0) return '0'
  return cny < 0.01 ? cny.toFixed(4) : cny.toFixed(2)
}

/** 运行时长（毫秒 → `2m 13s` / `45s`）；进度条 ETA 与运行摘要共用，格式统一。 */
export function formatDuration(milliseconds: number): string {
  if (!Number.isFinite(milliseconds) || milliseconds <= 0) return '0s'
  const seconds = Math.round(milliseconds / 1000)
  if (seconds < 60) return `${seconds}s`
  const minutes = Math.floor(seconds / 60)
  const remainder = seconds % 60
  return remainder ? `${minutes}m ${remainder}s` : `${minutes}m`
}

const CNY_FORMAT = new Intl.NumberFormat('zh-CN', {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
})

/** 配额 / 成本芯片用（两位小数固定）。 */
export function formatCny(value: number | null | undefined): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '¥—'
  return `¥${CNY_FORMAT.format(value)}`
}

const DATE_TIME_FORMAT = new Intl.DateTimeFormat('zh-CN', {
  dateStyle: 'medium',
  timeStyle: 'short',
})

/** ISO 时间 → 本地化展示（`2026年9月26日 10:00`）；非法输入返回占位。 */
export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? '—' : DATE_TIME_FORMAT.format(date)
}

export function formatBytes(size: number): string {
  if (size >= 1024 * 1024) return `${(size / 1024 / 1024).toFixed(1)} MB`
  if (size >= 1024) return `${(size / 1024).toFixed(0)} KB`
  return `${size} B`
}
