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

export function CitationsCard({ citations, verifiedCitations }: CitationsCardProps) {
  return (
    <section className="surface-card p-5 sm:p-6">
      <SectionHeading
        eyebrow="证据"
        title="引用校验"
        detail={`${verifiedCitations} / ${citations.length} 严格通过`}
      />
      {citations.length === 0 ? (
        <EmptyState icon="∅" title="没有引用记录" text="报告可能在引用校验前停止，或本轮未生成可校验引用。" />
      ) : (
        <div className="mt-5 grid gap-3 lg:grid-cols-2">
          {citations.map((citation, index) => (
            <CitationCard key={`${citation.source}-${index}`} citation={citation} index={index} />
          ))}
        </div>
      )}
    </section>
  )
}

export function SourcesReflection({ result }: { result: ResearchResult }) {
  return (
    <section className="grid gap-6 lg:grid-cols-2">
      {/* P1-7 移动端：`min-w-0` 必须有 —— grid 子项默认 `min-width:auto`，
          长 URL / 长单词会把列撑得比容器宽，整页出现横向滚动条。 */}
      <div className="surface-card min-w-0 p-5 sm:p-6">
        <SectionHeading eyebrow="来源" title="访问来源" detail={`${result.visited_sources.length} 个`} />
        <div className="mt-5 space-y-2">
          {result.visited_sources.length ? result.visited_sources.map((source, index) => (
            <SourceRow key={`${source}-${index}`} source={source} index={index} />
          )) : <EmptyState icon="∅" title="暂无来源" text="没有可展示的来源记录。" />}
        </div>
      </div>

      <div className="surface-card min-w-0 p-5 sm:p-6">
        <SectionHeading eyebrow="反思" title="决策轨迹" detail={`${result.reflection_log.length} 轮`} />
        <div className="mt-5 space-y-3">
          {result.reflection_log.length ? result.reflection_log.map((entry, index) => (
            <div key={index} className="surface-card-muted p-4">
              <div className="mb-2 flex items-center justify-between gap-2">
                <span className="text-xs font-semibold text-ink">第 {String(entry.depth ?? index + 1)} 轮判断</span>
                {entry.decision != null && <span className="rounded-md bg-stamp-green/10 px-2 py-1 text-[10px] font-semibold text-stamp-green">{String(entry.decision)}</span>}
              </div>
              <p className="text-xs leading-5 text-ink-muted">{reflectionSummary(entry)}</p>
            </div>
          )) : <EmptyState icon="∅" title="暂无决策轨迹" text="本轮没有可展示的 critic 记录。" />}
        </div>
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
