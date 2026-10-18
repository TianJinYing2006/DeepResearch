/** 结果区（R4a）：报告 / 引用校验 / 来源与决策 / 校验统计。 */
import { useEffect, useMemo, useState } from 'react'

import { ReportView } from '../../components/ReportView'
import { CitationCard, EmptyState, SectionHeading, SourceRow } from '../../components/ui'
import { formatUnknown, humanizeKey, reflectionSummary } from '../../lib/presentation'
import { filterCitations, type CitationFilter, type QualityVerdict } from '../../lib/quality'
import type { CitationResult, ResearchResult } from '../../types/agui'

type ReportCardProps = {
  result: ResearchResult
  runId: string | null
  outputUnderReview: boolean
  hasReport: boolean
  copyState: 'idle' | 'copied'
  exportState: 'idle' | 'exported' | 'failed'
  /** 需求 38 F2：PDF 异步作业状态（创建→轮询→下载；`working` 期间按钮禁用）。 */
  pdfState: 'idle' | 'working' | 'exported' | 'failed' | 'expired'
  /** 需求 38 F3：DOCX 同步导出状态。 */
  docxState: 'idle' | 'exported' | 'failed'
  onCopy: () => void
  onExport: () => void
  onExportPdf: () => void
  onExportDocx: () => void
  /** 需求 26：分享开关与入口（关闭时不渲染按钮）。 */
  shareEnabled?: boolean
  onShare?: () => void
}

/** 需求 35 R4：正文流式预览卡（报告完成前渐进渲染）。
 *
 * 终局到达后由 `ReportCard` 的完整报告**整段替换**（绝不拼接）；
 * 半截失败由后端发 `REPORT_RESET`，预览清空回到空态。 */
export function ReportPreviewCard({ preview, stopping }: { preview: string; stopping?: boolean }) {
  return (
    <section id="stage-report" className="surface-card overflow-hidden" data-testid="report-preview">
      <div className="flex flex-col justify-between gap-4 border-b border-rule px-5 py-5 sm:flex-row sm:items-center sm:px-6">
        <div>
          <p className="text-xs font-medium text-stamp-blue">报告</p>
          <h2 className="mt-1 text-xl font-semibold text-ink">研究报告</h2>
          <p className="mt-1 text-xs text-ink-muted">
            正文生成中… · {preview.length.toLocaleString('zh-CN')} 字符
            {stopping ? '（正在收尾）' : ''}
          </p>
        </div>
      </div>
      <div className="px-5 py-6 sm:px-8 sm:py-8">
        <ReportView report={preview} />
      </div>
    </section>
  )
}

export function ReportCard({ result, runId, outputUnderReview, hasReport, copyState, exportState, pdfState, docxState, onCopy, onExport, onExportPdf, onExportDocx, shareEnabled, onShare }: ReportCardProps) {
  return (
    <section id="stage-report" className="surface-card overflow-hidden">
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
        {/* 阶段栏「导出与分享」的锚点（验收点①）。这一排按钮就是该阶段的全部内容，
            所以锚点挂在这里而不是另起区块 —— 单起区块会得到一个空壳。 */}
        <div id="stage-export" className="flex flex-wrap gap-2">
          <button className="secondary-button !px-3 !py-2" type="button" onClick={onCopy} disabled={!hasReport || !result.report || outputUnderReview}>
            {copyState === 'copied' ? '已复制' : '复制正文'}
          </button>
          {/* P1-6：走后端导出（正文 + 审计元数据 + 引用清单），不是前端 Blob 那份纯正文 */}
          <button
            className="secondary-button !px-3 !py-2"
            type="button"
            onClick={onExport}
            disabled={!runId || !hasReport || outputUnderReview}
            data-testid="export-button"
          >
            {exportState === 'exported' ? '已导出' : exportState === 'failed' ? '导出失败' : '导出 .md'}
          </button>
          {/* 需求 38 F2：PDF 走后端异步作业（专用 export 服务渲染；生成中禁用防重复提交） */}
          <button
            className="secondary-button !px-3 !py-2"
            type="button"
            onClick={onExportPdf}
            disabled={!runId || !hasReport || outputUnderReview || pdfState === 'working'}
            data-testid="export-pdf-button"
          >
            {pdfState === 'working'
              ? 'PDF 生成中…'
              : pdfState === 'exported'
                ? '已导出 PDF'
                : pdfState === 'expired'
                  ? 'PDF 已过期，重试'
                  : pdfState === 'failed'
                    ? 'PDF 导出失败'
                    : '导出 PDF'}
          </button>
          {/* 需求 38 F3：DOCX 同步导出（API 进程内生成；轻量无作业态） */}
          <button
            className="secondary-button !px-3 !py-2"
            type="button"
            onClick={onExportDocx}
            disabled={!runId || !hasReport || outputUnderReview}
            data-testid="export-docx-button"
          >
            {docxState === 'exported' ? '已导出 DOCX' : docxState === 'failed' ? 'DOCX 导出失败' : '导出 DOCX'}
          </button>
          {shareEnabled && onShare && (
            <button
              className="secondary-button !px-3 !py-2"
              type="button"
              onClick={onShare}
              disabled={!runId || !hasReport || outputUnderReview}
              data-testid="share-button"
            >
              分享
            </button>
          )}
        </div>
      </div>
      <div className="px-5 py-6 sm:px-8 sm:py-8">
        {outputUnderReview ? (
          <EmptyState icon="⚑" title="报告待人工复核" text="报告命中内容安全预检：复核通过或申诉处理前不开放查看与导出；如认为误判可提交申诉。" />
        ) : hasReport && result.report ? (
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
  quality: Pick<QualityVerdict, 'counts'>
}

/** 方向 B 签名：引用证据挂在**页边栏**（与报告并排、可滚动、可聚焦），
 *  阅读时视线不离开正文；窄屏自动落到报告下方。 */
export function EvidenceMargin({ citations, quality }: CitationsCardProps) {
  const [filter, setFilter] = useState<CitationFilter>('all')
  const filteredCitations = useMemo(() => filterCitations(citations, filter), [citations, filter])
  const { counts } = quality
  const options: Array<{ key: CitationFilter; label: string; count: number }> = [
    { key: 'all', label: '全部', count: counts.total },
    { key: 'verified', label: '严格通过', count: counts.verified },
    { key: 'relaxed', label: '宽松通过', count: counts.relaxed },
    { key: 'refuted', label: '确证失败', count: counts.refuted },
    { key: 'unknown', label: '校验未完成', count: counts.unknown },
  ]
  const detail = citations.length === 0
    ? '暂无引用'
    : `${counts.verified} / ${counts.total} 严格通过 · ${counts.refuted} 条确证失败 · ${counts.unknown} 条未完成`

  // 新结果到达时回到全量，避免上一轮的筛选造成「引用消失」的错觉。
  useEffect(() => setFilter('all'), [citations])

  const emptyCopy: Record<CitationFilter, { title: string; text: string }> = {
    all: { title: '暂无引用记录', text: '报告可能在引用校验前停止，或本轮未生成可校验引用。' },
    verified: { title: '暂无严格通过引用', text: '没有同时通过来源存在性和论断忠实度核验的引用。' },
    relaxed: { title: '暂无宽松通过引用', text: '没有仅以多源印证或宽松口径通过的引用。' },
    refuted: { title: '暂无确证失败引用', text: '当前没有被核验为不成立的引用。' },
    unknown: { title: '暂无未完成校验', text: '当前引用均已得到严格、宽松或失败结论。' },
  }
  const empty = emptyCopy[filter]
  return (
    <section className="surface-card p-4" aria-label="引用证据">
      <SectionHeading
        eyebrow="证据"
        title="引用校验"
        detail={detail}
      />
      {citations.length > 0 && (
        <div className="mt-4 grid grid-cols-2 gap-1 rounded-lg border border-rule bg-rule/20 p-1 sm:grid-cols-5" role="group" aria-label="引用状态筛选" data-testid="evidence-filters">
          {options.map((option) => (
            <button
              key={option.key}
              type="button"
              className={`min-w-0 rounded-md px-2 py-2 text-left text-[11px] font-semibold transition sm:text-center ${filter === option.key ? 'bg-sheet text-ink shadow-[0_1px_2px_rgba(23,27,25,0.08)]' : 'text-ink-muted hover:bg-sheet/70 hover:text-ink'}`}
              aria-pressed={filter === option.key}
              data-testid={`evidence-filter-${option.key}`}
              onClick={() => setFilter(option.key)}
            >
              <span className="block truncate">{option.label}</span>
              <span className="mt-0.5 block font-mono text-[10px] font-normal tabular-nums text-ink-muted">{option.count}</span>
            </button>
          ))}
        </div>
      )}
      {citations.length === 0 ? (
        <EmptyState compact icon="∅" title={empty.title} text={empty.text} />
      ) : filteredCitations.length === 0 ? (
        <EmptyState compact icon="⌕" title={empty.title} text={empty.text} />
      ) : (
        <ol className="mt-4 max-h-[58vh] space-y-2 overflow-y-auto pr-1" tabIndex={0}
            role="region" aria-label="引用列表">
          {filteredCitations.map((citation) => {
            const originalIndex = citations.indexOf(citation)
            return (
              <li key={`${citation.source}-${originalIndex}`}>
                <CitationCard citation={citation} index={originalIndex} />
              </li>
            )
          })}
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
