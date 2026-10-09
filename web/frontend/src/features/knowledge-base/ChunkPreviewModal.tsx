/** 分块预览弹窗（验收点③ · 切片 S6）：从 AccountPanel 抽出。
 *
 * 为什么单独成文件：
 * 分块预览是知识库面板里**唯一**会拉正文的弹窗（`GET /api/rag/docs/{id}/chunks`），
 * 它自带 4 个状态、1 个请求函数、1 个纯格式化函数，与账号/配额/上传三条线毫无耦合，
 * 是拆分收益最直接、风险最低的一块。
 *
 * ⚠️ 拆分纪律（不可破坏的一条）：
 * **「先落 `previewDoc` 再发请求」的顺序不能改**。弹窗受 `previewDoc` 驱动，
 * 靠「状态先到位 ⇒ 弹窗立刻出现 ⇒ `previewChunks === null && !previewError` 渲染骨架屏」
 * 实现「点预览马上有反应」。若改成等数据到达再挂载弹窗，骨架态和标题里的
 * 「（0 块）」首帧会一并消失（属视觉回归，当前无 e2e 覆盖）。
 *
 * 另注：`close()` **刻意不重置 `total`** —— 这是从原实现逐字搬过来的行为，
 * 保持「纯搬移、零行为差异」。`total` 只在 `previewDoc` 存在时被读取，
 * 而 `open()` 每次都会先把它置 0，因此该字段残留不可观测。
 */
import { useCallback, useEffect, useRef, useState } from 'react'

import { readErrorMessage } from '../../lib/api'
import Modal from '../../components/Modal'
import { SkeletonRows } from '../../components/ui'
import type { RagChunk } from '../../types/api'

/** 需求 23：locator → 可读定位（页码 / 幻灯片 / 工作表 / 行范围）。
 *
 * ⚠️ 逐字搬自 AccountPanel，**不要凭印象重写**：`slide` 的文案是「页幻灯片」、
 * 行范围读的是数组 `row_range`（不是 `row_start`/`row_end`）、兜底是「全文」。
 * 这三处都是 e2e 断言之外的静默行为面。 */
export function locatorLabel(locator: Record<string, unknown>): string {
  const parts: string[] = []
  if (typeof locator.page === 'number') parts.push(`第 ${locator.page} 页`)
  if (typeof locator.slide === 'number') parts.push(`第 ${locator.slide} 页幻灯片`)
  if (typeof locator.sheet === 'string') parts.push(`工作表 ${locator.sheet}`)
  const range = locator.row_range
  if (Array.isArray(range) && range.length === 2) parts.push(`行 ${range[0]}-${range[1]}`)
  return parts.join(' · ') || '全文'
}

export type ChunkPreviewTarget = { docId: string; source: string }

export type ChunkPreviewController = {
  previewDoc: ChunkPreviewTarget | null
  previewChunks: RagChunk[] | null
  previewTotal: number
  previewError: string
  open: (docId: string, source: string) => Promise<void>
  close: () => void
  /** 登出/注销清理用：把本切片持有的账号级状态全部归零。 */
  reset: () => void
}

/** 需求 23：分块预览（默认活动版本；locator 供引用回溯定位）。 */
export function useChunkPreview(): ChunkPreviewController {
  const [previewDoc, setPreviewDoc] = useState<ChunkPreviewTarget | null>(null)
  const [previewChunks, setPreviewChunks] = useState<RagChunk[] | null>(null)
  const [previewTotal, setPreviewTotal] = useState(0)
  const [previewError, setPreviewError] = useState('')
  const generationRef = useRef(0)

  useEffect(() => () => { generationRef.current += 1 }, [])

  const close = useCallback(() => {
    generationRef.current += 1
    setPreviewDoc(null)
    setPreviewChunks(null)
    setPreviewError('')
  }, [])

  const reset = useCallback(() => {
    generationRef.current += 1
    setPreviewDoc(null)
    setPreviewChunks(null)
    setPreviewTotal(0)
    setPreviewError('')
  }, [])

  const open = useCallback(async (docId: string, source: string) => {
    const generation = ++generationRef.current
    // 顺序即行为：先落目标再请求（见本文件顶部的拆分纪律）。
    setPreviewDoc({ docId, source })
    setPreviewChunks(null)
    setPreviewTotal(0)
    setPreviewError('')
    try {
      const response = await fetch(`/api/rag/docs/${encodeURIComponent(docId)}/chunks?limit=50`)
      if (generation !== generationRef.current) return
      if (!response.ok) {
        const message = await readErrorMessage(response)
        if (generation !== generationRef.current) return
        setPreviewError(message)
        return
      }
      const body = (await response.json()) as { total: number; chunks: RagChunk[] }
      if (generation !== generationRef.current) return
      setPreviewChunks(body.chunks)
      setPreviewTotal(body.total)
    } catch {
      if (generation !== generationRef.current) return
      setPreviewError('网络错误，请重试')
    }
  }, [])

  return { previewDoc, previewChunks, previewTotal, previewError, open, close, reset }
}

export function ChunkPreviewModal({ preview }: { preview: ChunkPreviewController }) {
  const { previewDoc, previewChunks, previewTotal, previewError, close } = preview
  if (!previewDoc) return null
  return (
    <Modal
      onClose={close}
      labelledBy="kb-preview-title"
      testId="kb-preview"
      overlayClassName="z-50 flex items-start justify-center overflow-y-auto bg-ink/35 p-4"
      panelClassName="surface-card my-6 w-full max-w-2xl p-6"
    >
      <div className="flex items-center justify-between">
        <h3 id="kb-preview-title" className="text-sm font-semibold text-ink">
          分块预览：{previewDoc.source}（{previewTotal} 块）
        </h3>
        <button type="button" className="text-xs text-ink-muted hover:text-ink" onClick={close}>
          关闭
        </button>
      </div>
      {previewError && <p role="alert" className="mt-3 text-sm text-stamp-red">{previewError}</p>}
      {previewChunks === null && !previewError && (
        <div className="mt-3"><SkeletonRows rows={4} /></div>
      )}
      {previewChunks && (
        <ul className="mt-3 max-h-[60vh] space-y-2 overflow-y-auto" data-testid="kb-chunk-list">
          {previewChunks.map((chunk) => (
            <li key={chunk.chunk_id} className="rounded-lg border border-rule bg-rule/30 px-3 py-2">
              <p className="text-[11px] text-ink-muted">
                #{chunk.chunk_index + 1} · {locatorLabel(chunk.locator)}
              </p>
              <p className="mt-1 whitespace-pre-wrap text-[13px] leading-relaxed text-ink">
                {chunk.text.length > 400 ? `${chunk.text.slice(0, 400)}…` : chunk.text}
              </p>
            </li>
          ))}
        </ul>
      )}
    </Modal>
  )
}
