/** 侧数据域（验收点③ 切片 S4+S5-state）：配额 / 知识库文档 / 知识库容量。
 *
 * 为什么抽出来：这三份数据在 AccountPanel 里原本是 4 个 `useState` + 1 个
 * `refreshSideData`，被 **9 个**互不相干的场景共用（上传完成、会话就绪、
 * run 边界、删文档、KB 写操作、登录成功、点 KB 开关、点刷新、邀请注册成功）。
 * 它们与账号会话、上传状态机、弹窗编排没有任何耦合，是纯粹的「服务端侧数据镜像」。
 *
 * ⚠️ 两条必须保住的语义（拆分前实测确认，改动会静默变更请求数）：
 *
 * 1. **`refresh()` 永远是一次打三个接口**，不是三个独立刷新。原实现就是
 *    `Promise.allSettled([quota, docs, usage])` 一次并发。若把三者拆成各自 mount 时
 *    再拉一次，单次触发会从 3 个请求涨到 6 个 —— 而它被 `kb-toggle`（可连点）与
 *    「每次上传完成」频繁调起。
 * 2. **`useUploads` 的宿主层级不得下移**。上传状态机的完成回调是 `refresh`，
 *    但状态机本身必须挂在**永不卸载**的外层；一旦移进 `kbOpen` 门控的面板，
 *    收起面板会 `xhr.abort()` 掉在途上传（详见拆分前风险清单 R1）。
 */
import { useCallback, useEffect, useRef, useState } from 'react'

import { readErrorMessage } from '../lib/api'
import type { Quota, RagDoc } from '../types/api'

export type KbUsage = { used_bytes: number; quota_bytes: number | null }

export type SideData = {
  quota: Quota | null
  docs: RagDoc[] | null
  docsError: string
  kbUsage: KbUsage | null
  /** 一次并发刷新三份侧数据；逐资源结算，任一失败不影响其余两份。 */
  refresh: () => Promise<void>
  /** 登出/注销清理：四份数据全部归零。 */
  clear: () => void
}

export function useSideData(): SideData {
  const [quota, setQuota] = useState<Quota | null>(null)
  const [docs, setDocs] = useState<RagDoc[] | null>(null)
  const [docsError, setDocsError] = useState('')
  const [kbUsage, setKbUsage] = useState<KbUsage | null>(null)
  const generationRef = useRef(0)

  useEffect(() => () => { generationRef.current += 1 }, [])

  // 审计 U25：原先用 `await Promise.all([...])` 且无 try/catch —— 三个接口里**任一**网络失败
  // 就让整个刷新以未捕获拒绝收场：另外两个已经拿到的好数据一起丢，调用点（`void refresh()`）
  // 也没人接这个拒绝。改用 allSettled 逐资源结算：成功几个更新几个，失败的那个单独置错误态。
  const refresh = useCallback(async () => {
    const generation = ++generationRef.current
    const [quotaResult, docsResult, usageResult] = await Promise.allSettled([
      fetch('/api/quota'), fetch('/api/rag/docs'), fetch('/api/rag/usage'),
    ])
    if (generation !== generationRef.current) return

    const quotaResp = quotaResult.status === 'fulfilled' ? quotaResult.value : null
    if (quotaResp?.ok) {
      const body = (await quotaResp.json()) as Quota
      if (generation !== generationRef.current) return
      setQuota(body)
    } else setQuota(null)

    const docsResp = docsResult.status === 'fulfilled' ? docsResult.value : null
    if (docsResp?.ok) {
      const body = (await docsResp.json()) as { docs: RagDoc[] }
      if (generation !== generationRef.current) return
      setDocs(body.docs)
      setDocsError('')
    } else {
      setDocs(null)
      // 请求本身被拒（断网/连接被断）时没有 Response 可读 ⇒ 用与其它网络错误一致的文案
      const message = docsResp ? await readErrorMessage(docsResp) : '网络错误，请重试'
      if (generation !== generationRef.current) return
      setDocsError(message)
    }

    const usageResp = usageResult.status === 'fulfilled' ? usageResult.value : null
    if (usageResp?.ok) {
      const body = (await usageResp.json()) as KbUsage
      if (generation !== generationRef.current) return
      setKbUsage(body)
    } else {
      setKbUsage(null)
    }
  }, [])

  const clear = useCallback(() => {
    generationRef.current += 1
    setQuota(null)
    setDocs(null)
    setDocsError('')
    setKbUsage(null)
  }, [])

  return { quota, docs, docsError, kbUsage, refresh, clear }
}
