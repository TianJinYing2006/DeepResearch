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

  const closeSource = useCallback(() => {
    sourceRef.current?.close()
    sourceRef.current = null
  }, [])

  useEffect(() => closeSource, [closeSource])

  const start = useCallback(async (
    topic: string,
    instructions: string,
    maxTotalHops: number,
    maxSubquestions: number,
    searchProvider?: string,
    enableArxiv?: boolean,
  ) => {
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
        headers: { 'Content-Type': 'application/json' },
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
    } catch (startError) {
      closeSource()
      setStatus('error')
      setConnectionStatus('closed')
      setError(toStructuredError(startError, 'start_failed', '启动研究失败'))
    }
  }, [closeSource])

  const cancel = useCallback(async () => {
    if (!runId || terminalRef.current) return
    setError(null)
    setStatus('stopping')
    try {
      const response = await fetch(`/api/research/${runId}/cancel`, { method: 'POST' })
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
    cancel,
  }
}
