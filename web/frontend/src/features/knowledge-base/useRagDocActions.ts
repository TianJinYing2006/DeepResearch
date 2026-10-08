/** 知识库文档操作态（验收点③ 切片 S5 · actions）：删除 / 重命名 / 重分块 / 重嵌入。
 *
 * 为什么单独成文件：这 6 个状态与 2 个请求函数只服务于「文档行的就地操作」，
 * 与账号会话、上传状态机、弹窗编排没有任何关系；它们原本和 29 个别的 state 挤在一起。
 *
 * 设计取舍：hook **连 setter 一起返回并沿用原名**。JSX 里既有读取（`renamingDocId === docId`）
 * 也有直接写入（进入/取消两步删除、开始重命名、输入框 onChange、Escape 退出），
 * 若只返回动作函数就必须逐个改写这些调用点 —— 那样 diff 大、风险高而收益为零。
 * 因此这里刻意暴露 setter，换取「调用方零改动」。
 *
 * ⚠️ 跨域依赖是**显式**的，不是隐藏的：
 * - `refresh`：写操作成功后要重载文档清单与容量（来自 `hooks/useSideData.ts`）。
 * - `onError`：**删除失败走的是账号条的错误通道**（原实现写 `barMessage`），
 *   不是知识库自己的 `kbActionError` —— 这个不对称是既有行为，已由拆分前风险清单 R6 记录，
 *   此处用回调参数把它摆到明面上，而不是让它继续藏在闭包里。
 */
import { useCallback, useState } from 'react'

import { csrfHeaders, readErrorMessage } from '../../lib/api'

export type RagDocActions = {
  deleteDocId: string | null
  setDeleteDocId: (id: string | null) => void
  deletingDoc: boolean
  renamingDocId: string | null
  setRenamingDocId: (id: string | null) => void
  renameValue: string
  setRenameValue: (value: string) => void
  kbBusyDocId: string | null
  kbActionError: string
  deleteDoc: (docId: string) => Promise<void>
  kbMutate: (docId: string, suffix: string, body?: unknown, method?: string) => Promise<void>
  /** 登出/注销清理：本切片持有的账号级状态全部归零。 */
  clear: () => void
}

export function useRagDocActions({ refresh, onError }: {
  refresh: () => Promise<void>
  onError: (message: string) => void
}): RagDocActions {
  const [deleteDocId, setDeleteDocId] = useState<string | null>(null)
  const [deletingDoc, setDeletingDoc] = useState(false)
  const [renamingDocId, setRenamingDocId] = useState<string | null>(null)
  const [renameValue, setRenameValue] = useState('')
  const [kbBusyDocId, setKbBusyDocId] = useState<string | null>(null)
  const [kbActionError, setKbActionError] = useState('')

  /** R7：删除知识库文档 —— 后端同步删向量并验证归零（失败 503，可稍后重试）。 */
  async function deleteDoc(docId: string) {
    setDeletingDoc(true)
    try {
      const response = await fetch(`/api/rag/docs?doc_id=${encodeURIComponent(docId)}`, {
        method: 'DELETE',
        headers: csrfHeaders(),
      })
      if (!response.ok) {
        onError(await readErrorMessage(response))
        return
      }
      setDeleteDocId(null)
      await refresh()
    } catch {
      onError('网络错误，请重试')
    } finally {
      setDeletingDoc(false)
    }
  }

  /** 需求 23：知识库写操作（重命名 PATCH / 重分块 / 重嵌入）。 */
  async function kbMutate(docId: string, suffix: string, body?: unknown, method = 'POST') {
    // 刻意读渲染闭包里的当前值（非 useCallback）—— 与原实现同为每次渲染重建，
    // 「上一个写操作未结束时不再发起新的」这条互斥语义必须保持。
    if (kbBusyDocId !== null) return
    setKbActionError('')
    setKbBusyDocId(docId)
    try {
      const response = await fetch(`/api/rag/docs${suffix}`, {
        method,
        headers: { 'Content-Type': 'application/json', ...csrfHeaders() },
        body: body === undefined ? undefined : JSON.stringify(body),
      })
      if (!response.ok) {
        setKbActionError(await readErrorMessage(response))
        return
      }
      setRenamingDocId(null)
      await refresh()
    } catch {
      setKbActionError('网络错误，请重试')
    } finally {
      setKbBusyDocId(null)
    }
  }

  const clear = useCallback(() => {
    setDeleteDocId(null)
    setDeletingDoc(false)
    setRenamingDocId(null)
    setRenameValue('')
    setKbBusyDocId(null)
    setKbActionError('')
  }, [])

  return {
    deleteDocId, setDeleteDocId, deletingDoc,
    renamingDocId, setRenamingDocId, renameValue, setRenameValue,
    kbBusyDocId, kbActionError,
    deleteDoc, kbMutate, clear,
  }
}
