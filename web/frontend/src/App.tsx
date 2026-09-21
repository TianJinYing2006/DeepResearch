import { type FormEvent, useMemo, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { ProgressBar } from './components/ProgressBar'
import {
  type ConnectionStatus,
  type StreamStatus,
  useResearchStream,
} from './hooks/useResearchStream'
import type {
  AguiEvent,
  CitationResult,
  DegradationEvent,
  RunFinishedEvent,
  StateDeltaEvent,
  StepFinishedEvent,
} from './types/agui'

const NODE_LABELS: Record<string, string> = {
  plan: '规划问题',
  research: '检索证据',
  critic: '评估缺口',
  revise: '修订查询',
  write: '撰写报告',
  validate: '校验引用',
  render: '渲染结果',
}

export default function App() {
  const [topic, setTopic] = useState('')
  const [instructions, setInstructions] = useState('')
  const [maxTotalHops, setMaxTotalHops] = useState(20)
  const [copyState, setCopyState] = useState<'idle' | 'copied'>('idle')
  const {
    runId,
    events,
    status,
    connectionStatus,
    error,
    result,
    progress,
    start,
    cancel,
  } = useResearchStream()

  const running = status === 'starting' || status === 'running' || status === 'stopping'
  const steps = useMemo(() => events.filter(isStepFinished), [events])
  const degradations = useMemo(() => events.filter(isDegradation), [events])
  const lastStep = steps[steps.length - 1]
  const lastDelta = useMemo(() => lastEventOfType(events, 'STATE_DELTA') as StateDeltaEvent | undefined, [events])
  const finished = useMemo(() => lastEventOfType(events, 'RUN_FINISHED') as RunFinishedEvent | undefined, [events])
  const timeline = useMemo(
    () => events.filter((event) => event.type !== 'STEP_STARTED').slice(-18).reverse(),
    [events],
  )

  const elapsedMs = steps.reduce((total, step) => total + step.duration_ms, 0)
  const tokenUsed = finished?.token_used ?? lastStep?.token_used ?? 0
  const sourceCount = result?.visited_sources.length ?? lastDelta?.visited_sources_count ?? 0
  const findingsCount = lastDelta?.findings_count ?? 0
  const verifiedCitations = result?.citations.filter((citation) => citation.verified).length ?? 0
  const ragSourceCount = result?.visited_sources.filter(isKnowledgeBaseSource).length ?? 0
  const currentActivity = latestActivity(events)
  const statusInfo = statusPresentation(status)
  const connectionInfo = connectionPresentation(connectionStatus)

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!topic.trim() || running) return
    void start(topic, instructions, maxTotalHops)
  }

  const copyReport = async () => {
    if (!result?.report) return
    await navigator.clipboard.writeText(result.report)
    setCopyState('copied')
    window.setTimeout(() => setCopyState('idle'), 1600)
  }

  const downloadReport = () => {
    if (!result?.report) return
    const file = new Blob([result.report], { type: 'text/markdown;charset=utf-8' })
    const url = URL.createObjectURL(file)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = `${safeFileName(topic || 'research-report')}.md`
    anchor.click()
    URL.revokeObjectURL(url)
  }

  return (
    <div className="min-h-screen">
      <header className="border-b border-white/[0.07] bg-[#07100f]/80 backdrop-blur-xl">
        <div className="mx-auto flex max-w-[1600px] items-center justify-between gap-4 px-4 py-4 sm:px-6 lg:px-8">
          <div className="flex items-center gap-3">
            <div className="grid h-10 w-10 place-items-center rounded-xl border border-emerald-300/20 bg-emerald-300/10 text-lg text-emerald-200 shadow-[inset_0_0_20px_rgba(52,211,153,0.08)]">
              ◈
            </div>
            <div>
              <p className="font-semibold tracking-tight text-white">DeepResearch</p>
              <p className="text-xs text-slate-500">可审计研究工作台</p>
            </div>
          </div>
          <div className="flex items-center gap-2 text-xs">
            <span className={`inline-flex items-center gap-2 rounded-full border px-3 py-1.5 ${connectionInfo.className}`}>
              <span className={`h-1.5 w-1.5 rounded-full ${connectionInfo.dotClass}`} />
              {connectionInfo.label}
            </span>
            <span className="hidden rounded-full border border-white/10 bg-white/[0.03] px-3 py-1.5 text-slate-500 sm:inline-flex">
              AG-UI 事件语义
            </span>
          </div>
        </div>
      </header>

      <main className="mx-auto grid max-w-[1600px] gap-6 px-4 py-6 sm:px-6 lg:px-8 xl:grid-cols-[360px_minmax(0,1fr)]">
        <aside className="space-y-5 xl:sticky xl:top-6 xl:self-start">
          <form className="surface-card p-5" onSubmit={handleSubmit}>
            <div className="mb-6 flex items-start justify-between gap-3">
              <div>
                <p className="text-sm font-semibold text-white">新研究</p>
                <p className="mt-1 text-xs leading-5 text-slate-500">描述问题，研究过程会实时推送到右侧。</p>
              </div>
              <span className="rounded-lg border border-emerald-300/15 bg-emerald-300/[0.07] px-2 py-1 text-[10px] font-bold uppercase tracking-[0.16em] text-emerald-200/80">
                Live
              </span>
            </div>

            <label className="field-label" htmlFor="topic">研究主题</label>
            <textarea
              id="topic"
              className="field-control min-h-28 resize-y"
              value={topic}
              onChange={(event) => setTopic(event.target.value)}
              placeholder="例如：生成式 AI 对企业知识管理的实际影响"
              disabled={running}
              maxLength={1000}
              required
            />

            <label className="field-label mt-5" htmlFor="instructions">附加要求 <span className="normal-case tracking-normal text-slate-600">（可选）</span></label>
            <textarea
              id="instructions"
              className="field-control min-h-24 resize-y"
              value={instructions}
              onChange={(event) => setInstructions(event.target.value)}
              placeholder="指定时间范围、关注维度、报告风格等"
              disabled={running}
              maxLength={2000}
            />

            <div className="mt-5 flex items-center justify-between">
              <label className="field-label mb-0" htmlFor="hops">最大检索跳数</label>
              <span className="rounded-lg bg-black/25 px-2.5 py-1 font-mono text-sm text-emerald-200">{maxTotalHops}</span>
            </div>
            <input
              id="hops"
              className="mt-3 w-full accent-emerald-400"
              type="range"
              min={1}
              max={50}
              value={maxTotalHops}
              onChange={(event) => setMaxTotalHops(Number(event.target.value))}
              disabled={running}
            />
            <div className="mt-1 flex justify-between text-[10px] text-slate-600">
              <span>快速 1</span>
              <span>深入 50</span>
            </div>

            <button className="primary-button mt-6 w-full" type="submit" disabled={running || !topic.trim()}>
              <span>{status === 'starting' ? '启动中' : '开始研究'}</span>
              <span aria-hidden="true">→</span>
            </button>

            {(status === 'running' || status === 'stopping') && (
              <button className="danger-button mt-3 w-full" type="button" onClick={() => void cancel()} disabled={status === 'stopping'}>
                <span>{status === 'stopping' ? '正在安全停止' : '停止研究'}</span>
              </button>
            )}

            {status === 'stopping' && (
              <p className="mt-3 text-xs leading-5 text-amber-100/70">
                取消请求已生效。当前节点会自然结束，系统不会再启动下一节点。
              </p>
            )}
          </form>

          <section className="surface-card p-5">
            <p className="text-xs font-semibold uppercase tracking-[0.16em] text-slate-500">运行边界</p>
            <div className="mt-4 space-y-3 text-xs leading-5 text-slate-400">
              <BoundaryItem title="核心逻辑" text="研究判断全部留在 Python 后端" />
              <BoundaryItem title="费用口径" text="只展示后端实值，缺失时不做估算" />
              <BoundaryItem title="取消语义" text="节点边界停止，不把取消记为故障" />
            </div>
          </section>

          <section className="surface-card p-5">
            <div className="flex items-center justify-between gap-3">
              <div>
                <p className="text-sm font-semibold text-white">文档摄取状态</p>
                <p className="mt-1 text-xs text-slate-500">本轮知识库参与情况</p>
              </div>
              <span className="rounded-full border border-white/10 px-2.5 py-1 font-mono text-xs text-slate-300">{ragSourceCount}</span>
            </div>
            <p className="mt-4 text-xs leading-5 text-slate-400">
              {result
                ? ragSourceCount > 0
                  ? `本轮命中 ${ragSourceCount} 个本地知识库来源。`
                  : '本轮结果未命中本地知识库来源。'
                : '研究完成后显示 RAG 文档命中情况；当前协议不伪造摄取进度。'}
            </p>
          </section>
        </aside>

        <div className="min-w-0 space-y-6">
          <section className="surface-card overflow-hidden">
            <div className="border-b border-white/[0.07] px-5 py-5 sm:px-6">
              <div className="flex flex-col justify-between gap-4 sm:flex-row sm:items-center">
                <div>
                  <div className="flex flex-wrap items-center gap-2">
                    <span className={`inline-flex rounded-full border px-2.5 py-1 text-[11px] font-semibold ${statusInfo.className}`}>
                      {statusInfo.label}
                    </span>
                    {runId && <span className="font-mono text-[11px] text-slate-600">RUN {runId}</span>}
                  </div>
                  <h1 className="mt-3 max-w-4xl text-2xl font-semibold tracking-tight text-white sm:text-3xl">
                    {topic.trim() || '把复杂问题变成可追溯的研究结论'}
                  </h1>
                  <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-500">
                    {currentActivity || '提交主题后，这里会展示每个研究阶段、实时降级和最终引用依据。'}
                  </p>
                </div>
              </div>
            </div>

            <div className="p-5 sm:p-6">
              <ProgressBar progress={progress} cancelling={status === 'stopping'} />
            </div>
          </section>

          {error && (
            <section className="rounded-2xl border border-rose-400/20 bg-rose-400/[0.07] px-5 py-4 text-sm text-rose-100">
              <div className="flex gap-3">
                <span aria-hidden="true">!</span>
                <div>
                  <p className="font-semibold">需要注意</p>
                  <p className="mt-1 text-rose-100/70">{error}</p>
                </div>
              </div>
            </section>
          )}

          <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <MetricCard label="已完成节点" value={String(steps.length)} detail={lastStep ? nodeLabel(lastStep.node) : '等待运行'} accent="emerald" />
            <MetricCard label="累计 Token" value={formatNumber(tokenUsed)} detail="费用未提供，不估算" accent="cyan" />
            <MetricCard label="发现 / 来源" value={`${formatNumber(findingsCount)} / ${formatNumber(sourceCount)}`} detail="实时证据规模" accent="violet" />
            <MetricCard label="运行时长" value={formatDuration(elapsedMs)} detail={lastStep ? `深度 ${lastStep.depth}` : '节点耗时累计'} accent="amber" />
          </section>

          <section className="grid gap-6 lg:grid-cols-[minmax(0,1.35fr)_minmax(300px,0.65fr)]">
            <div className="surface-card min-h-[360px] p-5 sm:p-6">
              <SectionHeading eyebrow="Live trace" title="研究活动" detail={`${events.length} 条事件`} />
              {timeline.length === 0 ? (
                <EmptyState icon="⌁" title="等待研究开始" text="事件会按最新优先排列，断线重连不会重新启动研究。" />
              ) : (
                <div className="mt-5 max-h-[520px] space-y-1 overflow-y-auto pr-1">
                  {timeline.map((event, index) => (
                    <TimelineItem key={`${event.type}-${events.length - index}`} event={event} />
                  ))}
                </div>
              )}
            </div>

            <div className="surface-card p-5 sm:p-6">
              <SectionHeading eyebrow="Transparency" title="降级与恢复" detail={`${degradations.length} 项`} />
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

          {result && (
            <>
              <section className="surface-card overflow-hidden">
                <div className="flex flex-col justify-between gap-4 border-b border-white/[0.07] px-5 py-5 sm:flex-row sm:items-center sm:px-6">
                  <div>
                    <p className="text-xs font-semibold uppercase tracking-[0.16em] text-emerald-300/65">Research report</p>
                    <h2 className="mt-1 text-xl font-semibold text-white">研究报告</h2>
                    <p className="mt-1 text-xs text-slate-500">Markdown 安全渲染 · {result.report.length.toLocaleString('zh-CN')} 字符</p>
                  </div>
                  <div className="flex gap-2">
                    <button className="secondary-button !px-3 !py-2" type="button" onClick={() => void copyReport()} disabled={!result.report}>
                      {copyState === 'copied' ? '已复制' : '复制'}
                    </button>
                    <button className="secondary-button !px-3 !py-2" type="button" onClick={downloadReport} disabled={!result.report}>
                      下载 .md
                    </button>
                  </div>
                </div>
                <div className="px-5 py-6 sm:px-8 sm:py-8">
                  {result.report ? (
                    <article className="report-prose mx-auto max-w-4xl">
                      <ReactMarkdown remarkPlugins={[remarkGfm]}>{result.report}</ReactMarkdown>
                    </article>
                  ) : (
                    <EmptyState icon="◌" title="暂无完整报告" text="运行在报告生成前停止，已完成的事件与统计仍保留。" />
                  )}
                </div>
              </section>

              <section className="surface-card p-5 sm:p-6">
                <SectionHeading
                  eyebrow="Evidence"
                  title="引用校验"
                  detail={`${verifiedCitations} / ${result.citations.length} 严格通过`}
                />
                {result.citations.length === 0 ? (
                  <EmptyState icon="∅" title="没有引用记录" text="报告可能在引用校验前停止，或本轮未生成可校验引用。" />
                ) : (
                  <div className="mt-5 grid gap-3 lg:grid-cols-2">
                    {result.citations.map((citation, index) => (
                      <CitationCard key={`${citation.source}-${index}`} citation={citation} index={index} />
                    ))}
                  </div>
                )}
              </section>

              <section className="grid gap-6 lg:grid-cols-2">
                <div className="surface-card p-5 sm:p-6">
                  <SectionHeading eyebrow="Sources" title="访问来源" detail={`${result.visited_sources.length} 个`} />
                  <div className="mt-5 space-y-2">
                    {result.visited_sources.length ? result.visited_sources.map((source, index) => (
                      <SourceRow key={`${source}-${index}`} source={source} index={index} />
                    )) : <EmptyState icon="∅" title="暂无来源" text="没有可展示的来源记录。" />}
                  </div>
                </div>

                <div className="surface-card p-5 sm:p-6">
                  <SectionHeading eyebrow="Reflection" title="决策轨迹" detail={`${result.reflection_log.length} 轮`} />
                  <div className="mt-5 space-y-3">
                    {result.reflection_log.length ? result.reflection_log.map((entry, index) => (
                      <div key={index} className="surface-card-muted p-4">
                        <div className="mb-2 flex items-center justify-between gap-2">
                          <span className="text-xs font-semibold text-slate-200">第 {String(entry.depth ?? index + 1)} 轮判断</span>
                          {entry.decision != null && <span className="rounded-md bg-emerald-300/10 px-2 py-1 text-[10px] font-semibold text-emerald-200">{String(entry.decision)}</span>}
                        </div>
                        <p className="text-xs leading-5 text-slate-500">{reflectionSummary(entry)}</p>
                      </div>
                    )) : <EmptyState icon="∅" title="暂无决策轨迹" text="本轮没有可展示的 critic 记录。" />}
                  </div>
                </div>
              </section>

              {Object.keys(result.validator_stats).length > 0 && (
                <section className="surface-card p-5 sm:p-6">
                  <SectionHeading eyebrow="Audit" title="校验统计" detail={`研究深度 ${result.depth}`} />
                  <div className="mt-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                    {Object.entries(result.validator_stats).map(([key, value]) => (
                      <div key={key} className="surface-card-muted p-4">
                        <p className="truncate text-[11px] uppercase tracking-[0.1em] text-slate-600">{humanizeKey(key)}</p>
                        <p className="mt-2 font-mono text-lg font-semibold text-slate-200">{formatUnknown(value)}</p>
                      </div>
                    ))}
                  </div>
                </section>
              )}
            </>
          )}
        </div>
      </main>
    </div>
  )
}

function BoundaryItem({ title, text }: { title: string; text: string }) {
  return (
    <div className="flex gap-3">
      <span className="mt-1 h-1.5 w-1.5 shrink-0 rounded-full bg-emerald-400/70" />
      <p><span className="font-medium text-slate-300">{title}：</span>{text}</p>
    </div>
  )
}

function MetricCard({ label, value, detail, accent }: { label: string; value: string; detail: string; accent: 'emerald' | 'cyan' | 'violet' | 'amber' }) {
  const accentClass = {
    emerald: 'from-emerald-400/20 text-emerald-200',
    cyan: 'from-cyan-400/20 text-cyan-200',
    violet: 'from-violet-400/20 text-violet-200',
    amber: 'from-amber-400/20 text-amber-200',
  }[accent]
  return (
    <div className={`surface-card bg-gradient-to-br ${accentClass} to-transparent p-4`}>
      <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-500">{label}</p>
      <p className="mt-2 font-mono text-2xl font-semibold tabular-nums">{value}</p>
      <p className="mt-1 truncate text-xs text-slate-500">{detail}</p>
    </div>
  )
}

function SectionHeading({ eyebrow, title, detail }: { eyebrow: string; title: string; detail: string }) {
  return (
    <div className="flex items-end justify-between gap-4">
      <div>
        <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-emerald-300/55">{eyebrow}</p>
        <h2 className="mt-1 text-lg font-semibold text-white">{title}</h2>
      </div>
      <p className="text-xs text-slate-600">{detail}</p>
    </div>
  )
}

function EmptyState({ icon, title, text }: { icon: string; title: string; text: string }) {
  return (
    <div className="grid min-h-48 place-items-center text-center">
      <div className="max-w-xs">
        <div className="mx-auto grid h-11 w-11 place-items-center rounded-xl border border-white/10 bg-white/[0.03] text-slate-500">{icon}</div>
        <p className="mt-3 text-sm font-medium text-slate-300">{title}</p>
        <p className="mt-1 text-xs leading-5 text-slate-600">{text}</p>
      </div>
    </div>
  )
}

function TimelineItem({ event }: { event: AguiEvent }) {
  const view = eventPresentation(event)
  return (
    <div className="group flex gap-3 rounded-xl px-2 py-3 transition hover:bg-white/[0.025]">
      <div className={`mt-0.5 grid h-7 w-7 shrink-0 place-items-center rounded-lg border text-xs ${view.iconClass}`}>{view.icon}</div>
      <div className="min-w-0 flex-1">
        <div className="flex items-center justify-between gap-3">
          <p className="truncate text-xs font-semibold text-slate-300">{view.title}</p>
          <span className="shrink-0 font-mono text-[10px] text-slate-700">{event.type}</span>
        </div>
        <p className="mt-1 text-xs leading-5 text-slate-500">{view.detail}</p>
      </div>
    </div>
  )
}

function CitationCard({ citation, index }: { citation: CitationResult; index: number }) {
  const verified = citation.verified
  return (
    <article className={`rounded-xl border p-4 ${verified ? 'border-emerald-300/15 bg-emerald-300/[0.04]' : 'border-amber-300/15 bg-amber-300/[0.04]'}`}>
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <span className="font-mono text-[10px] text-slate-600">#{index + 1}</span>
          <span className={`rounded-md px-2 py-1 text-[10px] font-semibold ${verified ? 'bg-emerald-300/10 text-emerald-200' : 'bg-amber-300/10 text-amber-200'}`}>
            {verified ? '严格通过' : citation.verified_relaxed ? '宽松通过' : '待复核'}
          </span>
        </div>
        <span className="font-mono text-xs text-slate-500">{Math.round(citation.confidence * 100)}%</span>
      </div>
      <p className="mt-3 text-sm leading-6 text-slate-300">{citation.claim}</p>
      <div className="mt-3 border-t border-white/[0.06] pt-3">
        <SourceLink source={citation.source} />
        {citation.note && <p className="mt-2 text-xs leading-5 text-slate-600">{citation.note}</p>}
      </div>
    </article>
  )
}

function SourceRow({ source, index }: { source: string; index: number }) {
  return (
    <div className="surface-card-muted flex items-center gap-3 p-3">
      <span className="grid h-7 w-7 shrink-0 place-items-center rounded-lg bg-black/20 font-mono text-[10px] text-slate-600">{index + 1}</span>
      <div className="min-w-0 flex-1"><SourceLink source={source} /></div>
      <span className="rounded-md bg-white/[0.04] px-2 py-1 text-[10px] uppercase text-slate-600">{sourceType(source)}</span>
    </div>
  )
}

function SourceLink({ source }: { source: string }) {
  if (/^https?:\/\//i.test(source)) {
    return <a className="block truncate text-xs text-emerald-300/80 hover:text-emerald-200" href={source} target="_blank" rel="noreferrer">{source}</a>
  }
  return <p className="truncate font-mono text-xs text-slate-400">{source}</p>
}

function isStepFinished(event: AguiEvent): event is StepFinishedEvent {
  return event.type === 'STEP_FINISHED'
}

function isDegradation(event: AguiEvent): event is DegradationEvent {
  return event.type === 'DEGRADATION'
}

function lastEventOfType(events: AguiEvent[], type: AguiEvent['type']): AguiEvent | undefined {
  for (let index = events.length - 1; index >= 0; index -= 1) {
    if (events[index].type === type) return events[index]
  }
  return undefined
}

function latestActivity(events: AguiEvent[]): string {
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index]
    if (event.type === 'STATE_DELTA') {
      const added = (event as StateDeltaEvent).progress_added
      const message = added[added.length - 1]?.msg
      if (message) return message
    }
  }
  return ''
}

function statusPresentation(status: StreamStatus) {
  return {
    idle: { label: '等待任务', className: 'border-white/10 bg-white/[0.03] text-slate-400' },
    starting: { label: '正在启动', className: 'border-cyan-300/20 bg-cyan-300/[0.08] text-cyan-200' },
    running: { label: '研究进行中', className: 'border-emerald-300/20 bg-emerald-300/[0.08] text-emerald-200' },
    stopping: { label: '正在安全停止', className: 'border-amber-300/20 bg-amber-300/[0.08] text-amber-200' },
    done: { label: '研究完成', className: 'border-emerald-300/20 bg-emerald-300/[0.08] text-emerald-200' },
    cancelled: { label: '已取消', className: 'border-amber-300/20 bg-amber-300/[0.08] text-amber-200' },
    error: { label: '运行失败', className: 'border-rose-300/20 bg-rose-300/[0.08] text-rose-200' },
  }[status]
}

function connectionPresentation(status: ConnectionStatus) {
  return {
    idle: { label: '等待实时流', className: 'border-white/10 bg-white/[0.03] text-slate-500', dotClass: 'bg-slate-600' },
    connecting: { label: '连接中', className: 'border-cyan-300/15 bg-cyan-300/[0.05] text-cyan-200', dotClass: 'animate-pulse bg-cyan-300' },
    live: { label: '实时连接', className: 'border-emerald-300/15 bg-emerald-300/[0.05] text-emerald-200', dotClass: 'bg-emerald-300 shadow-[0_0_8px_rgba(110,231,183,0.7)]' },
    reconnecting: { label: '正在重连', className: 'border-amber-300/15 bg-amber-300/[0.05] text-amber-200', dotClass: 'animate-pulse bg-amber-300' },
    closed: { label: '连接已关闭', className: 'border-white/10 bg-white/[0.03] text-slate-500', dotClass: 'bg-slate-600' },
  }[status]
}

function eventPresentation(event: AguiEvent) {
  switch (event.type) {
    case 'RUN_STARTED':
      return { icon: '▶', iconClass: 'border-cyan-300/15 bg-cyan-300/[0.07] text-cyan-200', title: '研究已启动', detail: `检索上限 ${String(event.max_total_hops ?? '—')} 跳` }
    case 'STEP_FINISHED': {
      const step = event as StepFinishedEvent
      return { icon: '✓', iconClass: 'border-emerald-300/15 bg-emerald-300/[0.07] text-emerald-200', title: nodeLabel(step.node), detail: `${formatDuration(step.duration_ms)} · 深度 ${step.depth} · ${formatNumber(step.token_used)} token` }
    }
    case 'STATE_DELTA': {
      const delta = event as StateDeltaEvent
      const message = delta.progress_added[delta.progress_added.length - 1]?.msg || '状态已更新'
      return { icon: '↗', iconClass: 'border-violet-300/15 bg-violet-300/[0.07] text-violet-200', title: '研究状态更新', detail: message }
    }
    case 'DEGRADATION': {
      const degradation = event as DegradationEvent
      return { icon: '!', iconClass: 'border-amber-300/15 bg-amber-300/[0.07] text-amber-200', title: `${degradation.component} 已降级`, detail: degradation.detail || degradation.reason }
    }
    case 'RUN_FINISHED': {
      const runFinished = event as RunFinishedEvent
      return { icon: '■', iconClass: runFinished.cancelled ? 'border-amber-300/15 bg-amber-300/[0.07] text-amber-200' : 'border-emerald-300/15 bg-emerald-300/[0.07] text-emerald-200', title: runFinished.cancelled ? '研究已在安全边界停止' : '研究已完成', detail: `${formatNumber(runFinished.token_used)} token · ${runFinished.degradation_count} 项降级` }
    }
    case 'RUN_ERROR':
      return { icon: '×', iconClass: 'border-rose-300/15 bg-rose-300/[0.07] text-rose-200', title: '研究运行失败', detail: String(event.message ?? '未知错误') }
    default:
      return { icon: '·', iconClass: 'border-white/10 bg-white/[0.03] text-slate-500', title: event.type, detail: '事件已接收' }
  }
}

function nodeLabel(node: string): string {
  return NODE_LABELS[node] ?? node
}

function formatNumber(value: number): string {
  return Number.isFinite(value) ? value.toLocaleString('zh-CN') : '0'
}

function formatDuration(milliseconds: number): string {
  if (!Number.isFinite(milliseconds) || milliseconds <= 0) return '0s'
  const seconds = Math.round(milliseconds / 1000)
  if (seconds < 60) return `${seconds}s`
  const minutes = Math.floor(seconds / 60)
  const remainder = seconds % 60
  return remainder ? `${minutes}m ${remainder}s` : `${minutes}m`
}

function safeFileName(value: string): string {
  return value.trim().replace(/[\\/:*?"<>|]+/g, '-').slice(0, 60) || 'research-report'
}

function isKnowledgeBaseSource(source: string): boolean {
  return source.startsWith('rag:') || source.startsWith('local://')
}

function sourceType(source: string): string {
  if (source.startsWith('rag:') || source.startsWith('local://')) return 'rag'
  if (source.includes('arxiv.org')) return 'arxiv'
  if (source.startsWith('code:')) return 'code'
  return 'web'
}

function reflectionSummary(entry: Record<string, unknown>): string {
  const summary = entry.gap ?? entry.knowledge_gap ?? entry.reason ?? entry.summary
  if (summary != null && String(summary).trim()) return String(summary)
  const rest = Object.entries(entry)
    .filter(([key]) => !['depth', 'decision'].includes(key))
    .map(([key, value]) => `${humanizeKey(key)}：${formatUnknown(value)}`)
  return rest.join('；') || '未提供更多说明。'
}

function humanizeKey(key: string): string {
  return key.replace(/_/g, ' ')
}

function formatUnknown(value: unknown): string {
  if (typeof value === 'number') {
    if (value >= 0 && value <= 1 && !Number.isInteger(value)) return `${Math.round(value * 100)}%`
    return value.toLocaleString('zh-CN')
  }
  if (typeof value === 'boolean') return value ? '是' : '否'
  if (value == null) return '—'
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}
