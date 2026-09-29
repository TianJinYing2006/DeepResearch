/** 知识库上传队列（R4b：从 AccountPanel 迁出的自包含状态机）。
 *
 * - XHR 上传（仅 XHR 能拿字节进度）→ 异步摄取轮询 → 终态；
 * - 每行可取消（abort XHR / 停止轮询）/ 移除 / 失败重试；
 * - 组件卸载统一 abort；失败保留 File 供重试，成功或移除才释放。
 */
import { useEffect, useRef, useState } from 'react'
import { csrfHeaders, errorMessageFromBody } from '../../lib/api'
import type { UploadItem } from '../../types/api'

type UploadHttpResult = { status: number; body: unknown }

function xhrUpload(
  file: File,
  onProgress: (percent: number) => void,
  onCreated?: (xhr: XMLHttpRequest) => void,
): Promise<UploadHttpResult> {
  return new Promise((resolve) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', '/api/rag/ingest')
    for (const [key, value] of Object.entries(csrfHeaders())) {
      xhr.setRequestHeader(key, value)
    }
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable && event.total > 0) {
        onProgress(Math.min(100, Math.round((event.loaded / event.total) * 100)))
      }
    }
    xhr.onload = () => {
      let body: unknown = null
      try {
        body = JSON.parse(xhr.responseText)
      } catch {
        body = null
      }
      resolve({ status: xhr.status, body })
    }
    xhr.onerror = () => resolve({ status: 0, body: null })
    xhr.onabort = () => resolve({ status: 0, body: null })
    const form = new FormData()
    form.append('file', file)
    onCreated?.(xhr)
    xhr.send(form)
  })
}

export function uploadLabel(item: UploadItem): string {
  switch (item.status) {
    case 'queued':
      return '排队中'
    case 'uploading':
      return `上传中 ${item.percent}%`
    case 'processing':
      return item.note ?? '处理中…'
    case 'done':
      return item.chunks ? `已入库（${item.chunks} 块）` : '已入库'
    case 'error':
      return `失败：${item.error ?? '未知错误'}`
    case 'cancelled':
      return '已取消'
    default:
      return item.status
  }
}

export function useUploads(onFinished: () => void) {
  const [uploads, setUploads] = useState<UploadItem[]>([])
  const [uploadState, setUploadState] = useState('')
  const uploadFilesRef = useRef<Map<string, File>>(new Map())
  const uploadXhrRef = useRef<Map<string, XMLHttpRequest>>(new Map())
  const uploadCancelledRef = useRef<Set<string>>(new Set())
  const mountedRef = useRef(true)

  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      for (const xhr of uploadXhrRef.current.values()) xhr.abort()
      uploadXhrRef.current.clear()
    }
  }, [])

  function updateUpload(id: string, patch: Partial<UploadItem>) {
    setUploads((previous) => previous.map((item) => (item.id === id ? { ...item, ...patch } : item)))
  }

  function cancelUpload(id: string) {
    uploadCancelledRef.current.add(id)
    uploadXhrRef.current.get(id)?.abort()
    uploadXhrRef.current.delete(id)
    updateUpload(id, { status: 'cancelled', percent: 0, chunks: undefined, note: undefined })
  }

  function removeUpload(id: string) {
    uploadCancelledRef.current.add(id)
    uploadXhrRef.current.get(id)?.abort()
    uploadXhrRef.current.delete(id)
    uploadFilesRef.current.delete(id)
    setUploads((previous) => previous.filter((item) => item.id !== id))
  }

  function retryUpload(item: UploadItem) {
    uploadCancelledRef.current.delete(item.id)
    void processUpload(item)
  }

  /** 登出/注销：清空队列与在途请求。 */
  function clearUploads() {
    for (const xhr of uploadXhrRef.current.values()) xhr.abort()
    uploadXhrRef.current.clear()
    uploadFilesRef.current.clear()
    uploadCancelledRef.current.clear()
    setUploads([])
    setUploadState('')
  }

  async function pollIngestion(ingestionId: string, shouldStop: () => boolean, attempts = 30): Promise<{
    status: string; chunks: number; source: string; error: string | null
  }> {
    // P0-8b：异步摄取 —— 每秒轮询直到终态（最长约 30s，之后提示稍后刷新）
    for (let index = 0; index < attempts; index += 1) {
      await new Promise((resolve) => setTimeout(resolve, 1000))
      if (shouldStop()) return { status: 'cancelled', chunks: 0, source: '', error: null }
      try {
        const response = await fetch(`/api/rag/ingestions/${ingestionId}`)
        if (!response.ok) continue
        const body = (await response.json()) as {
          status: string; chunks: number; source: string; error: string | null
        }
        if (body.status === 'ready' || body.status === 'rejected') return body
      } catch {
        /* 网络抖动继续轮询 */
      }
    }
    return { status: 'timeout', chunks: 0, source: '', error: null }
  }

  async function processUpload(item: UploadItem) {
    if (uploadCancelledRef.current.has(item.id)) return
    const file = uploadFilesRef.current.get(item.id)
    if (!file) return
    updateUpload(item.id, { status: 'uploading', percent: 0, error: undefined, note: undefined })
    const { status, body } = await xhrUpload(
      file,
      (percent) => {
        if (!uploadCancelledRef.current.has(item.id)) updateUpload(item.id, { percent })
      },
      (xhr) => uploadXhrRef.current.set(item.id, xhr),
    )
    uploadXhrRef.current.delete(item.id)
    if (!mountedRef.current) return
    if (uploadCancelledRef.current.has(item.id)) {
      updateUpload(item.id, { status: 'cancelled', percent: 0, chunks: undefined })
      return
    }
    if (status === 0) {
      // 失败条目保留 File：用户可「重试」，或「移除」释放引用
      updateUpload(item.id, { status: 'error', error: '网络错误，请重试' })
      setUploadState('网络错误，请重试')
      return
    }
    if (status < 200 || status >= 300) {
      const message = errorMessageFromBody(body, status)
      updateUpload(item.id, { status: 'error', error: message })
      setUploadState(message)
      return
    }
    const parsed = (body ?? {}) as { source?: string; chunks?: number; ingestion_id?: string }
    if (parsed.ingestion_id) {
      // P0-8b：异步摄取协议只有粗粒度状态 ⇒ 不显示百分比，只做不确定态动画
      updateUpload(item.id, { status: 'processing', percent: 100, chunks: undefined })
      const final = await pollIngestion(
        parsed.ingestion_id,
        () => uploadCancelledRef.current.has(item.id) || !mountedRef.current,
      )
      if (!mountedRef.current) return
      if (final.status === 'cancelled') {
        updateUpload(item.id, { status: 'cancelled', percent: 0 })
        return
      }
      if (final.status === 'ready') {
        updateUpload(item.id, { status: 'done', chunks: final.chunks })
        setUploadState(`已摄取 ${final.source || item.name}（${final.chunks} 块）`)
        uploadFilesRef.current.delete(item.id)
        return
      }
      if (final.status === 'rejected') {
        updateUpload(item.id, { status: 'error', error: final.error ?? '已拒绝' })
        setUploadState(`${final.source || item.name} 处理失败：${final.error ?? '已拒绝'}`)
        return
      }
      updateUpload(item.id, { status: 'processing', note: '仍在处理中，稍后刷新查看' })
      return
    }
    updateUpload(item.id, { status: 'done', percent: 100, chunks: parsed.chunks ?? 0 })
    setUploadState(`已摄取 ${parsed.source ?? item.name}（${parsed.chunks ?? 0} 块）`)
    uploadFilesRef.current.delete(item.id)
  }

  async function addFiles(fileList: FileList) {
    const files = Array.from(fileList)
    if (!files.length) return
    const items: UploadItem[] = files.map((file) => ({
      id: `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`,
      name: file.name,
      size: file.size,
      status: 'queued',
      percent: 0,
    }))
    items.forEach((item, index) => uploadFilesRef.current.set(item.id, files[index]))
    setUploads((previous) => [...previous, ...items])
    setUploadState('')
    for (const item of items) {
      await processUpload(item)
    }
    await onFinished()
  }

  return { uploads, uploadState, addFiles, retryUpload, removeUpload, cancelUpload, clearUploads }
}
