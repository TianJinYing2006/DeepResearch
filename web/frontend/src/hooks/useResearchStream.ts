import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { csrfHeaders, httpError, toStructuredError } from '../lib/api'
import {
  applyInteractionEvent,
  cardFromEvent,
  interactionFromSnapshot,
  isInteractionEvent,
} from '../lib/hitl'
import { applyEvent, computeProgress, emptySummary, type ProgressSummary } from '../lib/progress'
import { applyReportEvent } from '../lib/reportPreview'
import {
  AGUI_EVENT_TYPES,
  isAguiEvent,
  type AguiEvent,
  type FollowupAnswer,
  type FollowupEntry,
  type InteractionCard,
  type InteractionView,
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

export function useResearchStream() {
  const [runId, setRunId] = useState<string | null>(null)
  const [events, setEvents] = useState<AguiEvent[]>([])
  const [status, setStatus] = useState<StreamStatus>('idle')
  const [connectionStatus, setConnectionStatus] = useState<ConnectionStatus>('idle')
  const [error, setError] = useState<StructuredError | null>(null)
  const [result, setResult] = useState<ResearchResult | null>(null)
  /** 需求 35 R4：正文流式预览（最近一次 REPORT_RESET 之后的增量拼接；终局整段替换）。 */
  const [reportPreview, setReportPreview] = useState('')
  // 需求 36：当前待答交互卡片（非等待态为 null；刷新后经快照 interaction 字段恢复）
  const [interaction, setInteraction] = useState<InteractionCard | null>(null)
  const [interactionBusy, setInteractionBusy] = useState(false)
  const [interactionError, setInteractionError] = useState<StructuredError | null>(null)
  // 需求 36 H3：追问（不落库，仅本页展示；新 run 清空）
  const [followups, setFollowups] = useState<FollowupEntry[]>([])
  const [followupBusy, setFollowupBusy] = useState(false)
  const [followupError, setFollowupError] = useState<StructuredError | null>(null)
  // R5（审计 U50）：进度增量累加（每事件 O(1)），不再每次全量重扫事件流
  const [summary, setSummary] = useState<ProgressSummary>(emptySummary)
  const sourceRef = useRef<EventSource | null>(null)
  const summaryRef = useRef<ProgressSummary>(emptySummary())
  const terminalRef = useRef(false)
  // 需求 36：等待态标记 —— 服务端按 TBD-6 在卡片回放后主动关流，不是断线
  const awaitingRef = useRef(false)
  const interactionRef = useRef<InteractionCard | null>(null)
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
    // 新流（含刷新回放）从头累加：清空旧运行的进度摘要与正文预览
    summaryRef.current = emptySummary()
    setSummary(summaryRef.current)
    setReportPreview('')
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

        // 需求 35 R4：正文增量/重置只进预览缓冲，不进 events（时间线/计数保持活动语义）
        if (parsed.type === 'REPORT_DELTA' || parsed.type === 'REPORT_RESET') {
          setReportPreview((previous) => applyReportEvent(previous, parsed))
          return
        }

        // 需求 36：交互卡片/回答/过期/追问不进时间线 —— 卡片本身就是 UI；
        // 卡片状态经回放逐事件重建（断线重连/刷新恢复同一路径）。
        if (isInteractionEvent(parsed)) {
          if (parsed.type === 'CLARIFY_REQUESTED' || parsed.type === 'PLAN_PROPOSED') {
            const card = cardFromEvent(parsed)
            interactionRef.current = card
            setInteraction(card)
            awaitingRef.current = Boolean(card)
          } else if (
            parsed.type === 'CLARIFY_ANSWERED' ||
            parsed.type === 'PLAN_DECISION' ||
            parsed.type === 'CLARIFY_EXPIRED' ||
            parsed.type === 'PLAN_EXPIRED'
          ) {
            const next = applyInteractionEvent(interactionRef.current, parsed)
            interactionRef.current = next
            setInteraction(next)
            if (!next) awaitingRef.current = false
          }
          return
        }

        setEvents((previous) => [...previous, parsed])
        summaryRef.current = applyEvent(summaryRef.current, parsed)
        setSummary(summaryRef.current)

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
      if (terminalRef.current) return
      // 需求 36：等待态（卡片回放后服务端关流）不是断线，不显示「重连中」
      setConnectionStatus(awaitingRef.current ? 'closed' : 'reconnecting')
    }
  }, [closeSource])

  const start = useCallback(async (
    topic: string,
    instructions: string,
    profile: string,
  ) => {
    manualStartRef.current = true
    // R3（审计 U11）：启动失败不得摧毁上一份报告 —— 先记录当前视图，
    // 只有 POST 成功才清空并挂新流；失败时保留原状态与 sessionStorage 恢复点。
    const hadPrevious = runId !== null
    const previousStatus = status
    const previousConnection = connectionStatus
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
          profile,
        }),
      })
      if (!response.ok) throw await httpError(response, 'start_failed', '启动研究失败')
      const { run_id: nextRunId } = (await response.json()) as { run_id: string }

      closeSource()
      terminalRef.current = false
      awaitingRef.current = false
      interactionRef.current = null
      setInteraction(null)
      setInteractionError(null)
      setFollowups([])
      setFollowupError(null)
      seenEventIdsRef.current.clear()
      setEvents([])
      setResult(null)
      setRunId(nextRunId)
      setStatus('running')
      storeRun(nextRunId)
      attachStream(nextRunId)
    } catch (startError) {
      if (!hadPrevious) closeSource()
      setConnectionStatus(hadPrevious ? previousConnection : 'closed')
      setStatus(hadPrevious ? previousStatus : 'error')
      setError(toStructuredError(startError, 'start_failed', '启动研究失败'))
    }
  }, [attachStream, closeSource, connectionStatus, runId, status])

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
      const snapshot = (await response.json()) as {
        elapsed_seconds?: number
        interaction?: InteractionView | null
      }
      if (manualStartRef.current) return null
      terminalRef.current = false
      seenEventIdsRef.current.clear()
      setEvents([])
      setResult(null)
      setError(null)
      // 需求 36：恢复等待态卡片（刷新页面后仍能看到并回答）
      const restoredCard = interactionFromSnapshot(snapshot.interaction)
      interactionRef.current = restoredCard
      setInteraction(restoredCard)
      awaitingRef.current = Boolean(restoredCard)
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

  /** 需求 36：提交当前交互卡片的回答（clarification: reply；plan_approval: approve|revise）。

  成功后**重新挂流**（从 0 回放重建摘要/预览，seen 去重保证不重复计数），
  worker 认领后以 `Command(resume=)` 续跑，后续事件经新流送达。 */
  const answerInteraction = useCallback(async (action: string, text: string) => {
    const card = interactionRef.current
    if (!runId || !card) return
    setInteractionBusy(true)
    setInteractionError(null)
    try {
      const response = await fetch(`/api/research/${runId}/interaction`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...csrfHeaders() },
        body: JSON.stringify({ interaction_id: card.interaction_id, action, text }),
      })
      if (!response.ok) throw await httpError(response, 'interaction_failed', '提交回答失败')
      awaitingRef.current = false
      terminalRef.current = false
      interactionRef.current = null
      setInteraction(null)
      seenEventIdsRef.current.clear()
      setEvents([])
      attachStream(runId)
    } catch (submitError) {
      setInteractionError(
        toStructuredError(submitError, 'interaction_failed', '提交失败，请重试'),
      )
    } finally {
      setInteractionBusy(false)
    }
  }, [attachStream, runId])

  /** 需求 36 H3：报告后追问（不重搜；证据池 grounding 由服务端校验）。 */
  const askFollowup = useCallback(async (question: string) => {
    if (!runId || !question.trim()) return
    setFollowupBusy(true)
    setFollowupError(null)
    try {
      const response = await fetch(`/api/research/${runId}/follow-up`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...csrfHeaders() },
        body: JSON.stringify({ question: question.trim() }),
      })
      if (!response.ok) throw await httpError(response, 'followup_failed', '追问失败')
      const payload = (await response.json()) as FollowupAnswer
      setFollowups((previous) => [
        ...previous,
        {
          question: question.trim(),
          answer: payload.answer,
          citations: payload.citations ?? [],
          unverified_ids: payload.unverified_ids ?? [],
          grounded: Boolean(payload.grounded),
        },
      ])
    } catch (followupFailure) {
      setFollowupError(toStructuredError(followupFailure, 'followup_failed', '追问失败，请重试'))
    } finally {
      setFollowupBusy(false)
    }
  }, [runId])

  const progress = useMemo(() => computeProgress(summary), [summary])

  return {
    runId,
    events,
    status,
    connectionStatus,
    error,
    result,
    reportPreview,
    progress,
    interaction,
    interactionBusy,
    interactionError,
    answerInteraction,
    followups,
    followupBusy,
    followupError,
    askFollowup,
    start,
    resume,
    cancel,
  }
}
