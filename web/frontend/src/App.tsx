import { type FormEvent, useEffect, useMemo, useRef, useState } from 'react'
import AccountPanel from './components/AccountPanel'
import { BoundaryCard, LaunchForm, RagStatusCard } from './features/launch/LaunchPanels'
import { CitationsCard, ReportCard, SourcesReflection, ValidatorStats } from './features/report/ReportPanels'
import { ErrorCard, MetricsGrid, RunSummary, StatusCard, TimeoutCard, TracePanels } from './features/run/RunPanels'
import {
  type StreamStatus,
  useResearchStream,
} from './hooks/useResearchStream'
import { formatCost } from './lib/format'
import {
  connectionPresentation,
  isDegradation,
  isKnowledgeBaseSource,
  isStepFinished,
  lastEventOfType,
  latestActivity,
  statusPresentation,
} from './lib/presentation'
import type { LaunchParams } from './types/api'
import type {
  RunFinishedEvent,
  RunOptions,
  RunStartedEvent,
  StateDeltaEvent,
} from './types/agui'

/** 上次实际发起参数（刷新恢复后「同参数重试」仍可用）。 */
const LAST_REQUEST_KEY = 'dr.lastRequest'

/** R3（审计 U43）：表单草稿 —— 刷新/误关页面不丢正在写的研究主题。 */
const DRAFT_KEY = 'dr.draft'

function readStoredDraft(): { topic: string; instructions: string } | null {
  try {
    const raw = sessionStorage.getItem(DRAFT_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw) as { topic?: unknown; instructions?: unknown }
    return {
      topic: typeof parsed?.topic === 'string' ? parsed.topic : '',
      instructions: typeof parsed?.instructions === 'string' ? parsed.instructions : '',
    }
  } catch {
    return null
  }
}

/** R1（审计 U3）：运行状态 → 读屏播报文案（idle 不播报）。 */
const STATUS_ANNOUNCEMENTS: Partial<Record<StreamStatus, string>> = {
  starting: '正在启动研究…',
  running: '研究进行中',
  stopping: '正在停止研究…',
  done: '研究完成',
  cancelled: '研究已取消',
  timeout: '研究已超时停止',
  error: '研究出错，请查看错误信息',
}

function readStoredLaunchParams(): LaunchParams | null {
  try {
    const raw = sessionStorage.getItem(LAST_REQUEST_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw) as Partial<LaunchParams>
    return typeof parsed?.topic === 'string' ? (parsed as LaunchParams) : null
  } catch {
    return null
  }
}

function storeLaunchParams(params: LaunchParams): void {
  try {
    sessionStorage.setItem(LAST_REQUEST_KEY, JSON.stringify(params))
  } catch {
    /* 隐私模式禁用 storage 时静默降级：只影响刷新后的「同参数重试」 */
  }
}

export default function App() {
  const [storedDraft] = useState(readStoredDraft)
  const [topic, setTopic] = useState(storedDraft?.topic ?? '')
  const [instructions, setInstructions] = useState(storedDraft?.instructions ?? '')
  const [copyState, setCopyState] = useState<'idle' | 'copied'>('idle')
  // P0 profile 固化：前端只选档位（quick / standard），底层参数由服务端固定。
  const [profile, setProfile] = useState('quick')
  const [options, setOptions] = useState<RunOptions | null>(null)
  // P6-A：是否开启鉴权（登录门开关；未开启时保持匿名可用）
  const [authRequired, setAuthRequired] = useState(false)
  // P1-7 重试：记住**上一次实际发起**的参数（不是当前表单值）——
  // 用户可能在运行期间改了滑块，重试必须重跑原来那次，否则「重试」名不副实。
  const [lastRequest, setLastRequest] = useState<LaunchParams | null>(null)
  const [exportState, setExportState] = useState<'idle' | 'exported' | 'failed'>('idle')
  // R1（审计 U3）：读屏播报 —— 状态迁移 / 复制 / 导出结果写进隐藏 live region
  const [announcement, setAnnouncement] = useState('')
  // 实时运行时长：从「发起研究」那一刻起用定时器走秒。
  // 原来是累加各节点 duration_ms ⇒ 只有节点完成才会跳变，等待 LLM 时看起来卡住。
  const [runStartedAt, setRunStartedAt] = useState<number | null>(null)
  const stoppedAtRef = useRef<number | null>(null)
  const {
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
  } = useResearchStream()

  // 拉可用选项：搜索源可用性与运行档位列表；拉不到不阻断（沿用本地默认）。
  useEffect(() => {
    let cancelled = false
    fetch('/api/options')
      .then((response) => (response.ok ? response.json() : null))
      .then((data: RunOptions | null) => {
        if (cancelled || !data) return
        setOptions(data)
        setAuthRequired(Boolean(data.auth_required))
        // 后端默认档位优先；老后端没下发 profiles 时保持 quick。
        if (data.default_profile) setProfile(data.default_profile)
      })
      .catch(() => { /* 保持本地默认，不阻断主流程 */ })
    return () => { cancelled = true }
  }, [])

  // 档位分段切换器：选中项下标用于平移高亮块。
  const profileOptions = options?.profiles ?? []
  const activeProfile = profileOptions.find((item) => item.value === profile)
  const activeProfileIndex = Math.max(
    profileOptions.findIndex((item) => item.value === profile),
    0,
  )

  // #9 刷新恢复：会话里若有活跃 run，先问后端画像再**从 0 回放**已发生事件。
  // 回放不产生新的研究/LLM 调用；后端进程重启后快照 404 ⇒ 自动回 idle。
  useEffect(() => {
    void (async () => {
      const restored = await resume()
      if (!restored) return
      // 时长对齐后端画像，刷新不把计时清零
      setRunStartedAt(Date.now() - restored.elapsedSeconds * 1000)
      const storedParams = readStoredLaunchParams()
      if (storedParams) setLastRequest(storedParams)
    })()
  }, [resume])

  const running = status === 'starting' || status === 'running' || status === 'stopping'

  // R5（审计 U50）：250ms 时钟已下沉到 DurationCard（局部渲染，页面隐藏时暂停）；
  // 这里只负责在停止时冻结一次结束时刻，供「总耗时」在终局缺少后端耗时字段时兜底。
  useEffect(() => {
    if (!runStartedAt) return
    if (!running && stoppedAtRef.current === null) stoppedAtRef.current = Date.now()
  }, [runStartedAt, running])

  const steps = useMemo(() => events.filter(isStepFinished), [events])
  const degradations = useMemo(() => events.filter(isDegradation), [events])
  const lastStep = steps[steps.length - 1]
  const lastDelta = useMemo(() => lastEventOfType(events, 'STATE_DELTA') as StateDeltaEvent | undefined, [events])
  const finished = useMemo(() => lastEventOfType(events, 'RUN_FINISHED') as RunFinishedEvent | undefined, [events])
  // P0-4：报告命中预检 ⇒ 事件流正文已脱敏，展示「待复核」而不是空报告
  const outputUnderReview = Boolean(finished?.output_under_review)
  // 完整活动：原先 .slice(-18) 会把早期事件挤掉，导致「之前的活动丢失」。
  // 容器本身已可滚动，这里不再截断。
  const timeline = useMemo(
    // 带上原始下标：reverse 后新事件会使所有位置后移，若用倒序下标当 key 会让
    // 每个条目都「变成另一个元素」而重载。绑定原始事件下标后 key 稳定。
    () => events
      .map((event, index) => ({ event, index }))
      .filter((entry) => entry.event.type !== 'STEP_STARTED')
      .reverse(),
    [events],
  )

  // 实时运行时长（终局兜底用；R5 起不再驱动全局重渲染 —— 时钟在 DurationCard 内部）
  const elapsedMs = runStartedAt ? Math.max(0, (stoppedAtRef.current ?? Date.now()) - runStartedAt) : 0
  // 成本估算由后端按 config.llm.pricing 计算（前端没有定价表，不能自己拍单价）
  const costLabel = finished?.cost_estimate_cny === undefined
    ? running ? '运行结束后给出估算' : '本次未提供估算'
    : `≈ ¥${formatCost(finished.cost_estimate_cny)}（按 output 单价的上界）`
  const tokenUsed = finished?.token_used ?? lastStep?.token_used ?? 0
  // P1-2：时限由后端下发（RUN_STARTED / /api/options），前端不自己拍默认值 ——
  // 否则改了 DR_RUN_TIMEOUT_SECONDS 前端还显示旧值，等于又造一个假数字。
  const timeoutSeconds = useMemo(() => {
    const started = events.find((event) => event.type === 'RUN_STARTED') as RunStartedEvent | undefined
    return started?.timeout_seconds ?? options?.run_timeout_seconds ?? null
  }, [events, options])
  const sourceCount = result?.visited_sources.length ?? lastDelta?.visited_sources_count ?? 0
  const findingsCount = lastDelta?.findings_count ?? 0
  const verifiedCitations = result?.citations.filter((citation) => citation.verified).length ?? 0
  const ragSources = useMemo(
    () => Array.from(new Set((result?.visited_sources ?? []).filter(isKnowledgeBaseSource))),
    [result],
  )
  const currentActivity = useMemo(() => latestActivity(events), [events])
  const statusInfo = statusPresentation(status)
  const connectionInfo = connectionPresentation(connectionStatus)

  useEffect(() => {
    const message = STATUS_ANNOUNCEMENTS[status]
    if (message) setAnnouncement(message)
  }, [status])

  useEffect(() => {
    if (copyState === 'copied') setAnnouncement('报告正文已复制到剪贴板')
  }, [copyState])

  useEffect(() => {
    if (exportState === 'exported') setAnnouncement('报告已开始下载')
    else if (exportState === 'failed') setAnnouncement('导出失败，请重试')
  }, [exportState])

  // R3（审计 U43）：草稿防抖落 sessionStorage + 离页强制 flush（刷新/关标签不丢）
  const draftRef = useRef({ topic, instructions })
  useEffect(() => {
    draftRef.current = { topic, instructions }
    const handle = window.setTimeout(() => {
      try {
        sessionStorage.setItem(DRAFT_KEY, JSON.stringify(draftRef.current))
      } catch {
        /* 只影响「刷新后保留草稿」，不影响本次输入 */
      }
    }, 500)
    return () => window.clearTimeout(handle)
  }, [topic, instructions])

  useEffect(() => {
    const flush = () => {
      try {
        sessionStorage.setItem(DRAFT_KEY, JSON.stringify(draftRef.current))
      } catch {
        /* 隐私模式禁用 storage 时静默降级 */
      }
    }
    window.addEventListener('pagehide', flush)
    window.addEventListener('beforeunload', flush)
    return () => {
      window.removeEventListener('pagehide', flush)
      window.removeEventListener('beforeunload', flush)
    }
  }, [])

  const launch = (params: LaunchParams) => {
    // 从发起时刻开始计时（不是等第一个节点完成）
    setRunStartedAt(Date.now())
    stoppedAtRef.current = null
    setLastRequest(params)
    setExportState('idle')
    storeLaunchParams(params)
    void start(params.topic, params.instructions, params.profile)
  }

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!topic.trim() || running) return
    launch({
      topic,
      instructions,
      profile,
    })
  }

  // P1-7 重试：**重新发起一次同样参数的研究**，不是断点续跑 ——
  // D-19 定的是前台模型、不做持久化，进程里没有可续跑的中间态。
  const handleRetry = () => {
    if (!lastRequest || running) return
    launch(lastRequest)
  }

  const copyReport = async () => {
    if (!result?.report) return
    await navigator.clipboard.writeText(result.report)
    setCopyState('copied')
    window.setTimeout(() => setCopyState('idle'), 1600)
  }

  // P1-6：改走后端导出 —— 前端 Blob 那份只有正文，脱离页面后无从自证来源；
  // 后端版本带 run_id / run_status / 降级条数等审计元数据与引用清单。
  const exportReport = async () => {
    if (!runId) return
    try {
      const response = await fetch(`/api/research/${runId}/report?format=md`)
      if (!response.ok) {
        setExportState('failed')
        return
      }
      const blob = await response.blob()
      const url = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = `deepresearch-${runId}.md`
      anchor.click()
      URL.revokeObjectURL(url)
      setExportState('exported')
      window.setTimeout(() => setExportState('idle'), 1600)
    } catch {
      setExportState('failed')
    }
  }

  return (
    <div className="min-h-dvh">
      {/* R4a（审计 U20）：跳过导航直达主内容（键盘/读屏） */}
      <a href="#main"
         className="sr-only focus:not-sr-only focus:absolute focus:left-3 focus:top-3 focus:z-50 focus:rounded-lg focus:bg-stamp-blue focus:px-3 focus:py-2 focus:text-sm focus:font-semibold focus:text-white">
        跳到主内容
      </a>
      {/* R1（审计 U3）：状态 / 复制 / 导出的读屏播报通道（视觉隐藏） */}
      <div className="sr-only" role="status" aria-live="polite" data-testid="live-region">
        {announcement}
      </div>
      <header className="border-b border-t-2 border-b-rule border-t-stamp-blue bg-paper/95 backdrop-blur-sm">
        <div className="mx-auto flex max-w-[1600px] items-center justify-between gap-4 px-4 py-4 sm:px-6 lg:px-8">
          <div className="flex items-center gap-3">
            <div className="grid h-10 w-10 place-items-center rounded-lg border border-stamp-blue/30 bg-stamp-blue/10 text-lg text-stamp-blue shadow-[inset_0_0_0_3px_rgba(30,90,216,0.07)]"
                 aria-hidden="true">
              ◈
            </div>
            <div>
              <p className="font-semibold tracking-tight text-ink">DeepResearch</p>
              <p className="text-xs text-ink-muted">可审计研究工作台</p>
            </div>
          </div>
          <div className="flex items-center gap-2 text-xs">
            <span className={`inline-flex items-center gap-2 rounded-full border px-3 py-1.5 ${connectionInfo.className}`}>
              <span className={`h-1.5 w-1.5 rounded-full ${connectionInfo.dotClass}`} aria-hidden="true" />
              {connectionInfo.label}
            </span>
            <span className="hidden rounded-full border border-rule bg-rule/30 px-3 py-1.5 text-ink-muted sm:inline-flex">
              AG-UI 事件语义
            </span>
          </div>
        </div>
        <div className="mx-auto max-w-[1600px] px-4 pb-3 sm:px-6 lg:px-8">
          <AccountPanel authRequired={authRequired} activeRunId={runId ?? null} running={running} />
        </div>
      </header>

      <main id="main"
            className="mx-auto grid max-w-[1600px] gap-5 px-4 py-5 sm:gap-6 sm:px-6 sm:py-6 lg:px-8 xl:grid-cols-[360px_minmax(0,1fr)]">
        {/* R4a（审计 U16）：左栏自滚动，键盘可直接聚焦滚动 */}
        <aside className="space-y-5 xl:sticky xl:top-6 xl:max-h-[calc(100vh-3rem)] xl:self-start xl:overflow-y-auto xl:pr-1"
               tabIndex={0} role="region" aria-label="研究导航">
          <LaunchForm
            topic={topic}
            instructions={instructions}
            profile={profile}
            profileOptions={profileOptions}
            activeProfile={activeProfile}
            activeProfileIndex={activeProfileIndex}
            running={running}
            status={status}
            onTopicChange={setTopic}
            onInstructionsChange={setInstructions}
            onProfileChange={setProfile}
            onSubmit={handleSubmit}
            onCancel={() => void cancel()}
          />
          <BoundaryCard />
          <RagStatusCard result={result} ragSources={ragSources} />
        </aside>

        <div className="min-w-0 space-y-6">
          <StatusCard
            statusInfo={statusInfo}
            runId={runId}
            topic={topic}
            currentActivity={currentActivity}
            progress={progress}
            cancelling={status === 'stopping'}
          />

          {error && (
            <ErrorCard error={error} lastRequest={lastRequest} running={running} onRetry={handleRetry} />
          )}

          {status === 'timeout' && <TimeoutCard timeoutSeconds={timeoutSeconds} />}

          {finished && (
            <RunSummary
              finished={finished}
              elapsedMs={elapsedMs}
              stepsCount={steps.length}
              outputUnderReview={outputUnderReview}
            />
          )}

          <MetricsGrid
            stepsCount={steps.length}
            lastStep={lastStep}
            tokenUsed={tokenUsed}
            costLabel={costLabel}
            findingsCount={findingsCount}
            sourceCount={sourceCount}
            startedAt={runStartedAt}
            timeoutSeconds={timeoutSeconds}
            running={running}
          />

          <TracePanels
            timeline={timeline}
            degradations={degradations}
            eventsCount={events.length}
            running={running}
          />

          {result && (
            <>
              <ReportCard
                result={result}
                runId={runId}
                outputUnderReview={outputUnderReview}
                copyState={copyState}
                exportState={exportState}
                onCopy={() => void copyReport()}
                onExport={() => void exportReport()}
              />
              <CitationsCard citations={result.citations} verifiedCitations={verifiedCitations} />
              <SourcesReflection result={result} />
              <ValidatorStats stats={result.validator_stats} depth={result.depth} />
            </>
          )}
        </div>
      </main>
    </div>
  )
}
