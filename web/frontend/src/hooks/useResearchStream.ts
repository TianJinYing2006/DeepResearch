import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { computeProgress, summarize } from '../lib/progress'
import {
  AGUI_EVENT_TYPES,
  isAguiEvent,
  type AguiEvent,
  type ResearchResult,
  type RunFinishedEvent,
  type StructuredError,
} from '../types/agui'

/**
 * P6-A：登录态下的写操作需要 CSRF 双提交头（与后端 dr_csrf Cookie 一致）。
 * 未登录 / 未开启鉴权时 Cookie 不存在，返回空对象，行为与 P4 之前完全一致。
 */
function csrfHeaders(): Record<string, string> {
  const match = document.cookie.match(/(?:^|;\s*)dr_csrf=([^;]+)/)
  return match ? { 'X-CSRF-Token': decodeURIComponent(match[1]) } : {}
}

export type StreamStatus =
  | 'idle'
  | 'starting'
  | 'running'
  | 'stopping'
  | 'done'
  /** P1-2：到时限被闸停 —— 既不是「完成」也不是「用户取消」，单独一态 */
  | 'timeout'
  | 'cancelled'
  | 'error'
export type ConnectionStatus = 'idle' | 'connecting' | 'live' | 'reconnecting' | 'closed'

/** 本标签页最近一次 run 的会话存储键（`sessionStorage`：只在本标签页存活，新标签页不串台）。

语义是「最近一次」而不是「活跃中」：**终局后保留** ⇒ 刷新页面仍可回看最终报告或错误卡；
进程重启后快照 404 时清空；手动发起新研究会覆盖。 */
const ACTIVE_RUN_KEY = 'dr.lastRunId'

function readStoredRun(): string | null {
  try {
    return sessionStorage.getItem(ACTIVE_RUN_KEY)
  } catch {
    return null
  }
}

function storeRun(runId: string): void {
  try {
    sessionStorage.setItem(ACTIVE_RUN_KEY, runId)
  } catch {
    /* 隐私模式禁用 storage 时静默降级：只影响「刷新后恢复」，不影响本次运行 */
  }
}

function clearStoredRun(): void {
  try {
    sessionStorage.removeItem(ACTIVE_RUN_KEY)
  } catch {
    /* 同上 */
  }
}

/** 把任意来源的错误归一成 `StructuredError`（P1-5）。

 后端已统一返回 `{code, message, component, node, detail, retryable, hint}`；
 但网络中断、JSON 解析失败这类**前端侧**错误没有后端载荷 ⇒ 在这里补齐同构字段，
 让错误卡片只需处理一种形状，不必到处判断「这次有没有 code」。 */
function toStructuredError(value: unknown, code: string, hint: string): StructuredError {
  const message = value instanceof Error ? value.message : typeof value === 'string' ? value : ''
  if (value && typeof value === 'object' && 'code' in value) {
    const parsed = value as Partial<StructuredError>
    return {
      code: parsed.code ?? code,
      message: parsed.message ?? message,
      component: parsed.component ?? null,
      node: parsed.node ?? null,
      detail: parsed.detail ?? null,
      retryable: parsed.retryable ?? false,
      hint: parsed.hint ?? hint,
    }
  }
  return {
    code,
    message: message || hint,
    component: null,
    node: null,
    detail: null,
    retryable: false,
    hint,
  }
}

async function httpError(response: Response, code: string, hint: string): Promise<StructuredError> {
  let body: { detail?: unknown } | null = null
  try {
    body = (await response.json()) as { detail?: unknown }
  } catch {
    body = null
  }
  // FastAPI 的结构化 `detail` 是**对象**；历史版本 / 第三方中间件可能是字符串。
  const detail = body?.detail
  if (detail && typeof detail === 'object') {
    return toStructuredError(detail, code, hint)
  }
  return toStructuredError(
    typeof detail === 'string' ? new Error(detail) : new Error(`${hint}（HTTP ${response.status}）`),
    code,
    hint,
  )
}

export function useResearchStream() {
  const [runId, setRunId] = useState<string | null>(null)
  const [events, setEvents] = useState<AguiEvent[]>([])
  const [status, setStatus] = useState<StreamStatus>('idle')
  const [connectionStatus, setConnectionStatus] = useState<ConnectionStatus>('idle')
  const [error, setError] = useState<StructuredError | null>(null)
  const [result, setResult] = useState<ResearchResult | null>(null)
  const sourceRef = useRef<EventSource | null>(null)
  const terminalRef = useRef(false)
  const seenEventIdsRef = useRef(new Set<string>())
  // 本页是否手动发起过研究：防止「恢复上一场运行」的异步请求抢在手动发起之后回填
  const manualStartRef = useRef(false)

  const closeSource = useCallback(() => {
    sourceRef.current?.close()
    sourceRef.current = null
  }, [])

  useEffect(() => closeSource, [closeSource])

  const attachStream = useCallback((nextRunId: string) => {
    closeSource()
    const source = new EventSource(`/api/research/${nextRunId}/stream`)
    sourceRef.current = source

    const handleEvent = (rawEvent: Event) => {
      const message = rawEvent as MessageEvent<string>
      if (message.lastEventId) {
        if (seenEventIdsRef.current.has(message.lastEventId)) return
        seenEventIdsRef.current.add(message.lastEventId)
      }

      try {
        const parsed: unknown = JSON.parse(message.data)
        if (!isAguiEvent(parsed)) throw new Error('未知事件类型')
        setEvents((previous) => [...previous, parsed])

        if (parsed.type === 'RUN_FINISHED') {
          const finished = parsed as RunFinishedEvent
          terminalRef.current = true
          setResult(finished.result)
          setStatus(
            finished.cancelled
              ? 'cancelled'
              : finished.stop_reason === 'timeout'
                ? 'timeout'
                : 'done',
          )
          setConnectionStatus('closed')
          source.close()
        } else if (parsed.type === 'RUN_ERROR') {
          terminalRef.current = true
          setError(toStructuredError(parsed, 'run_error', '研究运行失败，可调整参数后重试'))
          setStatus('error')
          setConnectionStatus('closed')
          source.close()
        }
      } catch (eventError) {
        terminalRef.current = true
        setError(toStructuredError(eventError, 'event_parse_failed', '事件解析失败，请刷新页面后重试'))
        setStatus('error')
        setConnectionStatus('closed')
        source.close()
      }
    }

    for (const eventType of AGUI_EVENT_TYPES) {
      source.addEventListener(eventType, handleEvent)
    }
    source.onopen = () => setConnectionStatus('live')
    source.onerror = () => {
      if (!terminalRef.current) setConnectionStatus('reconnecting')
    }
  }, [closeSource])

  const start = useCallback(async (
    topic: string,
    instructions: string,
    maxTotalHops: number,
    maxSubquestions: number,
    searchProvider?: string,
    enableArxiv?: boolean,
  ) => {
    manualStartRef.current = true
    closeSource()
    terminalRef.current = false
    seenEventIdsRef.current.clear()
    setEvents([])
    setRunId(null)
    setResult(null)
    setError(null)
    setStatus('starting')
    setConnectionStatus('connecting')

    try {
      const response = await fetch('/api/research', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...csrfHeaders() },
        body: JSON.stringify({
          topic: topic.trim(),
          instructions: instructions.trim(),
          max_total_hops: maxTotalHops,
          max_subquestions: maxSubquestions,
          search_provider: searchProvider,
          enable_arxiv: enableArxiv,
        }),
      })
      if (!response.ok) throw await httpError(response, 'start_failed', '启动研究失败')
      const { run_id: nextRunId } = (await response.json()) as { run_id: string }

      setRunId(nextRunId)
      setStatus('running')
      storeRun(nextRunId)
      attachStream(nextRunId)
    } catch (startError) {
      closeSource()
      clearStoredRun()
      setStatus('error')
      setConnectionStatus('closed')
      setError(toStructuredError(startError, 'start_failed', '启动研究失败'))
    }
  }, [attachStream, closeSource])

  /** 刷新后恢复：读 sessionStorage 的 run_id → 快照确认存在 → 从 0 回放全部帧。

  可恢复：进程存活期间产生的**全部**事件帧（后端 `_frames` 内存保留，事件 id = 帧下标），
  **终局后同样保留** ⇒ 刷新仍能回看最终报告或错误卡。
  不可恢复：后端进程重启（D-19 内存态、不做持久化）⇒ 快照 404，清存储回 idle；
  关标签页后在新标签打开也恢复不了（sessionStorage 按标签页隔离，D-19 明确接受该代价）。 */
  const resume = useCallback(async (): Promise<{ runId: string; elapsedSeconds: number } | null> => {
    if (manualStartRef.current) return null
    const stored = readStoredRun()
    if (!stored) return null
    try {
      const response = await fetch(`/api/research/${stored}`)
      if (!response.ok) {
        clearStoredRun()
        return null
      }
      const snapshot = (await response.json()) as { elapsed_seconds?: number }
      if (manualStartRef.current) return null
      terminalRef.current = false
      seenEventIdsRef.current.clear()
      setEvents([])
      setResult(null)
      setError(null)
      setRunId(stored)
      setStatus('running')
      setConnectionStatus('connecting')
      attachStream(stored)
      return { runId: stored, elapsedSeconds: Number(snapshot.elapsed_seconds) || 0 }
    } catch {
      // 网络错误：保留存储（可能只是临时断开），本次不恢复
      return null
    }
  }, [attachStream])

  const cancel = useCallback(async () => {
    if (!runId || terminalRef.current) return
    setError(null)
    setStatus('stopping')
    try {
      const response = await fetch(`/api/research/${runId}/cancel`, {
        method: 'POST',
        headers: csrfHeaders(),
      })
      if (!response.ok) throw await httpError(response, 'cancel_failed', '取消请求失败')
    } catch (cancelError) {
      if (!terminalRef.current) setStatus('running')
      // 取消失败**不覆盖**运行状态：研究还在继续，用户仍可再点一次停止。
      setError(toStructuredError(cancelError, 'cancel_failed', '取消请求失败，研究仍在继续'))
    }
  }, [runId])

  const progress = useMemo(() => computeProgress(summarize(events)), [events])

  return {
    runId,
    events,
    status,
    connectionStatus,
    error,
    result,
    progress,
    start,
    resume,
    cancel,
  }
}
