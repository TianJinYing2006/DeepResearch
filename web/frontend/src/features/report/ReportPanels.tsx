/** 结果区（R4a）：报告 / 引用校验 / 来源与决策 / 校验统计。 */
import { ReportView } from '../../components/ReportView'
import { CitationCard, EmptyState, SectionHeading, SourceRow } from '../../components/ui'
import { formatUnknown, humanizeKey, reflectionSummary } from '../../lib/presentation'
import type { CitationResult, ResearchResult } from '../../types/agui'

type ReportCardProps = {
  result: ResearchResult
  runId: string | null
  outputUnderReview: boolean
  copyState: 'idle' | 'copied'
  exportState: 'idle' | 'exported' | 'failed'
  onCopy: () => void
  onExport: () => void
}

export function ReportCard({ result, runId, outputUnderReview, copyState, exportState, onCopy, onExport }: ReportCardProps) {
  return (
    <section className="surface-card overflow-hidden">
      <div className="flex flex-col justify-between gap-4 border-b border-rule px-5 py-5 sm:flex-row sm:items-center sm:px-6">
        <div>
          <p className="text-xs font-medium text-stamp-blue">报告</p>
          <h2 className="mt-1 text-xl font-semibold text-ink" data-testid="report-heading">研究报告</h2>
          <p className="mt-1 text-xs text-ink-muted">
            {outputUnderReview
              ? '报告命中内容安全预检，正在等待人工复核'
              : `Markdown 安全渲染 · ${result.report.length.toLocaleString('zh-CN')} 字符`}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button className="secondary-button !px-3 !py-2" type="button" onClick={onCopy} disabled={!result.report || outputUnderReview}>
            {copyState === 'copied' ? '已复制' : '复制正文'}
          </button>
          {/* P1-6：走后端导出（正文 + 审计元数据 + 引用清单），不是前端 Blob 那份纯正文 */}
          <button
            className="secondary-button !px-3 !py-2"
            type="button"
            onClick={onExport}
            disabled={!runId || outputUnderReview}
            data-testid="export-button"
          >
            {exportState === 'exported' ? '已导出' : exportState === 'failed' ? '导出失败' : '导出 .md'}
          </button>
        </div>
      </div>
      <div className="px-5 py-6 sm:px-8 sm:py-8">
        {outputUnderReview ? (
          <EmptyState icon="⚑" title="报告待人工复核" text="报告命中内容安全预检：复核通过或申诉处理前不开放查看与导出；如认为误判可提交申诉。" />
        ) : result.report ? (
          <ReportView report={result.report} />
        ) : (
          <EmptyState icon="◌" title="暂无完整报告" text="运行在报告生成前停止，已完成的事件与统计仍保留。" />
        )}
      </div>
    </section>
  )
}

type CitationsCardProps = {
  citations: CitationResult[]
  verifiedCitations: number
}

/** 方向 B 签名：引用证据挂在**页边栏**（与报告并排、可滚动、可聚焦），
 *  阅读时视线不离开正文；窄屏自动落到报告下方。 */
export function EvidenceMargin({ citations, verifiedCitations }: CitationsCardProps) {
  return (
    <section className="surface-card p-4" aria-label="引用证据">
      <SectionHeading
        eyebrow="证据"
        title="引用校验"
        detail={`${verifiedCitations} / ${citations.length} 严格通过`}
      />
      {citations.length === 0 ? (
        <p className="mt-3 text-xs leading-5 text-ink-muted">
          没有引用记录：报告可能在引用校验前停止，或本轮未生成可校验引用。
        </p>
      ) : (
        <ol className="mt-4 max-h-[58vh] space-y-2 overflow-y-auto pr-1" tabIndex={0}
            role="region" aria-label="引用列表">
          {citations.map((citation, index) => (
            <li key={`${citation.source}-${index}`}>
              <CitationCard citation={citation} index={index} />
            </li>
          ))}
        </ol>
      )}
    </section>
  )
}

/** 页边栏的来源清单（紧凑版）。 */
export function SourcesList({ sources }: { sources: string[] }) {
  return (
    <section className="surface-card p-4" aria-label="访问来源">
      <SectionHeading eyebrow="来源" title="访问来源" detail={`${sources.length} 个`} />
      {sources.length === 0 ? (
        <p className="mt-3 text-xs leading-5 text-ink-muted">没有可展示的来源记录。</p>
      ) : (
        <div className="mt-4 max-h-64 space-y-2 overflow-y-auto pr-1" tabIndex={0}
             role="region" aria-label="来源列表">
          {sources.map((source, index) => (
            <SourceRow key={`${source}-${index}`} source={source} index={index} />
          ))}
        </div>
      )}
    </section>
  )
}

/** 主列：决策轨迹（critic 记录）。 */
export function ReflectionList({ log }: { log: Array<Record<string, unknown>> }) {
  return (
    <section className="surface-card p-5 sm:p-6">
      <SectionHeading eyebrow="反思" title="决策轨迹" detail={`${log.length} 轮`} />
      <div className="mt-5 space-y-3">
        {log.length ? log.map((entry, index) => (
          <div key={index} className="surface-card-muted p-4">
            <div className="mb-2 flex items-center justify-between gap-2">
              <span className="text-xs font-semibold text-ink">第 {String(entry.depth ?? index + 1)} 轮判断</span>
              {entry.decision != null && <span className="rounded-md bg-stamp-green/10 px-2 py-1 text-[10px] font-semibold text-stamp-green">{String(entry.decision)}</span>}
            </div>
            <p className="text-pretty text-xs leading-5 text-ink-muted">{reflectionSummary(entry)}</p>
          </div>
        )) : <EmptyState icon="∅" title="暂无决策轨迹" text="本轮没有可展示的 critic 记录。" />}
      </div>
    </section>
  )
}

export function ValidatorStats({ stats, depth }: { stats: Record<string, unknown>; depth: number }) {
  if (Object.keys(stats).length === 0) return null
  return (
    <section className="surface-card p-5 sm:p-6">
      <SectionHeading eyebrow="审计" title="校验统计" detail={`研究深度 ${depth}`} />
      <div className="mt-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {Object.entries(stats).map(([key, value]) => (
          <div key={key} className="surface-card-muted p-4">
            <p className="truncate text-[11px] text-ink-muted">{humanizeKey(key)}</p>
            <p className="mt-2 font-mono text-lg font-semibold text-ink">{formatUnknown(value)}</p>
          </div>
        ))}
      </div>
    </section>
  )
}
