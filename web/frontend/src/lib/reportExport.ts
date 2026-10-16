/**
 * 报告导出的共享动作（需求 38）：主视图与历史面板**同源**，避免两套实现漂移。
 *
 * - 同步格式（md / docx）：`GET /report?format=` 直接下载（后端在 API 进程内生成）；
 * - PDF：异步作业（`POST /export-jobs` → 轮询 → 授权下载；专用 export 服务渲染）；
 * - 失败语义：同步返回错误文案（`null` = 成功）；PDF 返回终态（expired 可重新申请）。
 */
import { csrfHeaders, readErrorMessage } from './api'
import { filenameFromDisposition } from './download'

export type SyncExportFormat = 'md' | 'docx'
export type PdfExportState = 'exported' | 'failed' | 'expired'

function triggerDownload(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  URL.revokeObjectURL(url)
}

/** 同步格式导出（md/docx）：成功返回 `null`，失败返回用户可读文案。 */
export async function downloadSyncReport(
  runId: string,
  format: SyncExportFormat,
): Promise<string | null> {
  try {
    const response = await fetch(`/api/research/${runId}/report?format=${format}`)
    if (!response.ok) return await readErrorMessage(response)
    const blob = await response.blob()
    const filename = filenameFromDisposition(response.headers.get('content-disposition'))
      ?? `deepresearch-${runId}.${format}`
    triggerDownload(blob, filename)
    return null
  } catch {
    return '网络错误，请重试'
  }
}

/** PDF 异步作业：创建（幂等复用）→ 轮询（1s × 60，覆盖 30s 渲染硬超时）→ 授权下载。 */
export async function downloadPdfReport(
  runId: string,
  pollIntervalMs = 1000,
  maxAttempts = 60,
): Promise<PdfExportState> {
  try {
    const created = await fetch(`/api/research/${runId}/export-jobs`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...csrfHeaders() },
      body: JSON.stringify({ kind: 'pdf' }),
    })
    if (!created.ok) return 'failed'
    const { export_id: exportId } = (await created.json()) as { export_id: string }
    let status = ''
    for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
      const probe = await fetch(`/api/research/${runId}/export-jobs/${exportId}`)
      if (!probe.ok) return 'failed'
      status = ((await probe.json()) as { status: string }).status
      if (status === 'SUCCEEDED' || status === 'FAILED' || status === 'EXPIRED') break
      await new Promise((resolve) => window.setTimeout(resolve, pollIntervalMs))
    }
    if (status === 'EXPIRED') return 'expired'
    if (status !== 'SUCCEEDED') return 'failed'
    const response = await fetch(`/api/research/${runId}/export-jobs/${exportId}/download`)
    if (!response.ok) return response.status === 410 ? 'expired' : 'failed'
    const filename = filenameFromDisposition(response.headers.get('content-disposition'))
      ?? `deepresearch-${runId}.pdf`
    triggerDownload(await response.blob(), filename)
    return 'exported'
  } catch {
    return 'failed'
  }
}
