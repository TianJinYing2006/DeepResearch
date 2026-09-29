/** 右栏运行看板（R4a/R5）：状态 / 错误 / 超时 / 摘要 / 指标 / 活动与降级。 */
import { useEffect, useRef, useState } from 'react'
import { ProgressBar } from '../../components/ProgressBar'
import { EmptyState, MetricCard, SectionHeading, SummaryItem, TimelineItem } from '../../components/ui'
import { formatCost, formatDuration, formatNumber } from '../../lib/format'
import { nodeLabel } from '../../lib/presentation'
import type { Progress } from '../../lib/progress'
import type { LaunchParams } from '../../types/api'
import type {
  AguiEvent,
  DegradationEvent,
  RunFinishedEvent,
  StepFinishedEvent,
  StructuredError,
} from '../../types/agui'

type StatusCardProps = {
  statusInfo: { label: string; className: string }
  runId: string | null
  topic: string
  currentActivity: string
  progress: Progress
  cancelling: boolean
}

export function StatusCard({ statusInfo, runId, topic, currentActivity, progress, cancelling }: StatusCardProps) {
  return (
    <section className="surface-card overflow-hidden">
      <div className="border-b border-white/[0.07] px-5 py-5 sm:px-6">
        <div className="flex flex-col justify-between gap-4 sm:flex-row sm:items-center">
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <span
                className={`inline-flex rounded-full border px-2.5 py-1 text-[11px] font-semibold ${statusInfo.className}`}
                data-testid="status-badge"
              >
                {statusInfo.label}
              </span>
              {runId && <span className="font-mono text-[11px] text-slate-400">RUN {runId}</span>}
            </div>
            <h1 className="mt-3 max-w-4xl text-2xl font-semibold tracking-tight text-white sm:text-3xl">
              {topic.trim() || '把复杂问题变成可追溯的研究结论'}
            </h1>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-400">
              {currentActivity || '提交主题后，这里会展示每个研究阶段、实时降级和最终引用依据。'}
            </p>
          </div>
        </div>
      </div>

      <div className="p-5 sm:p-6">
        <ProgressBar progress={progress} cancelling={cancelling} />
      </div>
    </section>
  )
}

type ErrorCardProps = {
  error: StructuredError
  lastRequest: LaunchParams | null
  running: boolean
  onRetry: () => void
}

export function ErrorCard({ error, lastRequest, running, onRetry }: ErrorCardProps) {
  return (
    <section
      role="alert"
      className="rounded-2xl border border-rose-400/20 bg-rose-400/[0.07] px-5 py-4 text-sm text-rose-100"
      data-testid="error-card"
    >
      <div className="flex gap-3">
        <span aria-hidden="true">!</span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <p className="font-semibold">需要注意</p>
            {/* P1-5 结构化错误：把 code / 归因组件 / 节点摆到台面上，
                用户不用从 message 文本里猜「这是谁的锅」 */}
            <span className="rounded-md bg-black/25 px-2 py-0.5 font-mono text-[10px] text-rose-200/80" data-testid="error-code">
              {error.code}
            </span>
            {error.component && (
              <span className="rounded-md bg-black/25 px-2 py-0.5 text-[10px] text-rose-200/70">
                {error.component}
              </span>
            )}
            {error.node && (
              <span className="rounded-md bg-black/25 px-2 py-0.5 font-mono text-[10px] text-rose-200/70">
                节点 {error.node}
              </span>
            )}
          </div>
          <p className="mt-1 text-rose-100/80">{error.message}</p>
          {error.detail && (
            <details className="mt-2" data-testid="error-detail">
              <summary className="cursor-pointer text-xs text-rose-200/60 hover:text-rose-100/80">
                错误详情
              </summary>
              <p className="mt-1 break-all font-mono text-[11px] leading-5 text-rose-200/55">
                {error.detail}
              </p>
            </details>
          )}
          {error.hint && (
            <p className="mt-2 text-xs leading-5 text-rose-100/70" data-testid="error-hint">
              {error.hint}
            </p>
          )}
          {lastRequest && (
            <button
              className="secondary-button mt-3 !px-3 !py-2"
              type="button"
              onClick={onRetry}
              disabled={running}
              data-testid="retry-button"
            >
              {running ? '运行中，暂不能重试' : '用同样参数重试'}
            </button>
          )}
        </div>
      </div>
    </section>
  )
}

export function TimeoutCard({ timeoutSeconds }: { timeoutSeconds: number | null }) {
  return (
    <section
      className="rounded-2xl border border-amber-300/20 bg-amber-300/[0.07] px-5 py-4 text-sm text-amber-100"
      data-testid="timeout-card"
      role="status"
    >
      <p className="font-semibold">研究已在时限处停止</p>
      <p className="mt-1 text-xs leading-5 text-amber-100/75">
        {timeoutSeconds === null
          ? '单次运行有墙钟时限，到点后在节点边界停止。'
          : `本次时限 ${formatDuration(timeoutSeconds * 1000)}（后端 DR_RUN_TIMEOUT_SECONDS）。`}
        停止发生在节点边界，最坏多等一个节点；已完成的节点与统计全部保留。
        超时既不算「完成」也不算「取消」，更不是故障 —— 不计入运行失败率。
      </p>
    </section>
  )
}

type RunSummaryProps = {
  finished: RunFinishedEvent
  elapsedMs: number
  stepsCount: number
  outputUnderReview: boolean
}

export function RunSummary({ finished, elapsedMs, stepsCount, outputUnderReview }: RunSummaryProps) {
  return (
    <section className="surface-card p-4 sm:p-5" data-testid="run-summary">
      <h2 className="text-xs font-semibold uppercase tracking-[0.16em] text-emerald-300/65">运行摘要</h2>
      <div className="mt-3 grid grid-cols-2 gap-x-4 gap-y-3 text-sm sm:grid-cols-3 lg:grid-cols-6">
        <SummaryItem label="总耗时" value={formatDuration((finished.elapsed_seconds ?? elapsedMs / 1000) * 1000)} />
        <SummaryItem label="完成节点" value={String(stepsCount)} />
        <SummaryItem label="检索跳数" value={String(finished.result?.depth ?? 0)} />
        <SummaryItem label="降级条目" value={String(finished.degradation_count ?? 0)} />
        <SummaryItem label="报告字数" value={outputUnderReview ? '待复核' : finished.result?.report ? formatNumber(finished.result.report.length) : '—'} />
        <SummaryItem label="成本估算" value={finished.cost_estimate_cny == null ? '—' : `≈ ¥${formatCost(finished.cost_estimate_cny)}`} />
      </div>
    </section>
  )
}

type MetricsGridProps = {
  stepsCount: number
  lastStep: StepFinishedEvent | undefined
  tokenUsed: number
  costLabel: string
  findingsCount: number
  sourceCount: number
  startedAt: number | null
  timeoutSeconds: number | null
  running: boolean
}

/** R5（审计 U50）：运行时长时钟下沉到本卡片 —— 每 250ms 只重渲染这一张卡；
 *  页面隐藏（切标签/最小化）时暂停计时，避免无谓渲染。 */
function DurationCard({ startedAt, running, timeoutSeconds, lastDepth }: {
  startedAt: number | null
  running: boolean
  timeoutSeconds: number | null
  lastDepth: number | null
}) {
  const [now, setNow] = useState(() => Date.now())
  const stoppedAtRef = useRef<number | null>(null)

  useEffect(() => {
    if (!running) {
      if (stoppedAtRef.current === null) stoppedAtRef.current = Date.now()
      return
    }
    stoppedAtRef.current = null
    let id: number | null = null
    const start = () => {
      if (id !== null) return
      setNow(Date.now())
      id = window.setInterval(() => setNow(Date.now()), 250)
    }
    const stop = () => {
      if (id !== null) {
        window.clearInterval(id)
        id = null
      }
    }
    const onVisibility = () => {
      if (document.hidden) stop()
      else start()
    }
    start()
    document.addEventListener('visibilitychange', onVisibility)
    return () => {
      stop()
      document.removeEventListener('visibilitychange', onVisibility)
    }
  }, [running])

  const elapsedMs = startedAt ? Math.max(0, (stoppedAtRef.current ?? now) - startedAt) : 0
  const remainingMs = timeoutSeconds === null ? null : Math.max(0, timeoutSeconds * 1000 - elapsedMs)
  return (
    <MetricCard
      label="运行时长"
      value={formatDuration(elapsedMs)}
      detail={
        running && remainingMs !== null
          ? `剩余约 ${formatDuration(remainingMs)}`
          : lastDepth !== null
            ? `深度 ${lastDepth}`
            : '自发起时刻起'
      }
      accent="amber"
    />
  )
}

export function MetricsGrid({
  stepsCount, lastStep, tokenUsed, costLabel, findingsCount, sourceCount,
  startedAt, timeoutSeconds, running,
}: MetricsGridProps) {
  return (
    <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
      <MetricCard label="已完成节点" value={String(stepsCount)} detail={lastStep ? nodeLabel(lastStep.node) : '等待运行'} accent="emerald" />
      <MetricCard label="累计 Token" value={formatNumber(tokenUsed)} detail={costLabel} accent="brand" />
      <MetricCard label="发现 / 来源" value={`${formatNumber(findingsCount)} / ${formatNumber(sourceCount)}`} detail="实时证据规模" accent="violet" />
      <DurationCard
        startedAt={startedAt}
        running={running}
        timeoutSeconds={timeoutSeconds}
        lastDepth={lastStep?.depth ?? null}
      />
    </section>
  )
}

type TracePanelsProps = {
  timeline: Array<{ event: AguiEvent; index: number }>
  degradations: DegradationEvent[]
  eventsCount: number
  running: boolean
}

export function TracePanels({ timeline, degradations, eventsCount, running }: TracePanelsProps) {
  return (
    <section className="grid gap-6 lg:grid-cols-[minmax(0,1.35fr)_minmax(300px,0.65fr)]">
      <div className="surface-card min-h-[360px] p-5 sm:p-6">
        <SectionHeading eyebrow="实时轨迹" title="研究活动" detail={`${eventsCount} 条事件`} />
        {/* P1-7 移动端：窄屏留给活动流的高度更小，避免一屏全是时间线 */}
        {timeline.length === 0 ? (
          <EmptyState icon="⌁" title="等待研究开始" text="事件会按最新优先排列，断线重连不会重新启动研究。" />
        ) : (
          <div className="mt-5 max-h-[360px] space-y-1 overflow-y-auto pr-1 sm:max-h-[520px]"
               tabIndex={0} role="region" aria-label="研究活动事件流">
            {timeline.map(({ event, index }) => (
              <TimelineItem key={index} event={event} />
            ))}
          </div>
        )}
      </div>

      <div className="surface-card p-5 sm:p-6">
        <SectionHeading eyebrow="透明记录" title="降级与恢复" detail={`${degradations.length} 项`} />
        {degradations.length === 0 ? (
          <EmptyState icon="✓" title="暂无降级" text={running ? '如有工具或供应商降级，会在这里立即显示。' : '本次运行没有收到降级事件。'} />
        ) : (
          <div className="mt-5 space-y-3">
            {degradations.map((event, index) => (
              <div key={`${event.component}-${index}`} className="rounded-xl border border-amber-300/15 bg-amber-300/[0.06] p-4">
                <div className="flex items-center justify-between gap-2">
                  <span className="text-xs font-semibold text-amber-100">{event.component}</span>
                  <span className="rounded-md bg-black/20 px-2 py-1 text-[10px] text-amber-200/70">{event.reason}</span>
                </div>
                <p className="mt-2 text-xs leading-5 text-slate-400">{event.detail || '未提供详情'}</p>
                <p className="mt-2 text-[11px] text-amber-200/60">回退：{event.fallback_action || '已由后端处理'}</p>
              </div>
            ))}
          </div>
        )}
      </div>
    </section>
  )
}
