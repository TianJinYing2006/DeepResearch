import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { computeProgress, summarize } from '../lib/progress'
import {
  AGUI_EVENT_TYPES,
  isAguiEvent,
  type AguiEvent,
  type ResearchResult,
  type RunFinishedEvent,
} from '../types/agui'

export type StreamStatus = 'idle' | 'starting' | 'running' | 'stopping' | 'done' | 'cancelled' | 'error'
export type ConnectionStatus = 'idle' | 'connecting' | 'live' | 'reconnecting' | 'closed'

async function responseError(response: Response, fallback: string): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: string }
    return body.detail || `${fallback}（HTTP ${response.status}）`
  } catch {
    return `${fallback}（HTTP ${response.status}）`
  }
}

export function useResearchStream() {
  const [runId, setRunId] = useState<string | null>(null)
  const [events, setEvents] = useState<AguiEvent[]>([])
  const [status, setStatus] = useState<StreamStatus>('idle')
  const [connectionStatus, setConnectionStatus] = useState<ConnectionStatus>('idle')
  const [error, setError] = useState<string | null>(null)
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
      if (!response.ok) throw new Error(await responseError(response, '启动研究失败'))
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
            setStatus(finished.cancelled ? 'cancelled' : 'done')
            setConnectionStatus('closed')
            source.close()
          } else if (parsed.type === 'RUN_ERROR') {
            terminalRef.current = true
            setError(String(parsed.message || '研究运行失败'))
            setStatus('error')
            setConnectionStatus('closed')
            source.close()
          }
        } catch (eventError) {
          terminalRef.current = true
          setError(eventError instanceof Error ? `事件解析失败：${eventError.message}` : '事件解析失败')
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
      setError(startError instanceof Error ? startError.message : '启动研究失败')
    }
  }, [closeSource])

  const cancel = useCallback(async () => {
    if (!runId || terminalRef.current) return
    setError(null)
    setStatus('stopping')
    try {
      const response = await fetch(`/api/research/${runId}/cancel`, { method: 'POST' })
      if (!response.ok) throw new Error(await responseError(response, '取消请求失败'))
    } catch (cancelError) {
      if (!terminalRef.current) setStatus('running')
      setError(cancelError instanceof Error ? `${cancelError.message}，研究仍在继续` : '取消请求失败，研究仍在继续')
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
