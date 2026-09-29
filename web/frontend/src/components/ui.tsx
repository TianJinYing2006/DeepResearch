/** 通用展示原语（R4a：从 App.tsx 迁出；纯展示，无业务状态）。
 *  R5：高频列表行（时间线/引用/来源/指标卡）用 memo 收敛重渲染；
 *  长列表用 CSS `content-visibility:auto` 跳过屏外布局（无需引入虚拟列表依赖）。 */
import { memo } from 'react'
import { eventPresentation, sourceType } from '../lib/presentation'
import type { AguiEvent, CitationResult } from '../types/agui'

export function BoundaryItem({ title, text }: { title: string; text: string }) {
  return (
    <div className="flex gap-3">
      <span className="mt-1 h-1.5 w-1.5 shrink-0 rounded-full bg-stamp-green" aria-hidden="true" />
      <p><span className="font-medium text-ink">{title}：</span>{text}</p>
    </div>
  )
}

export const MetricCard = memo(function MetricCard({ label, value, detail, accent }: { label: string; value: string; detail: string; accent: 'emerald' | 'brand' | 'violet' | 'amber' }) {
  // 方向 B：无渐变 —— 左侧 2px 色条表达语义，其余保持纸面
  const accentClass = {
    emerald: 'border-l-stamp-green',
    brand: 'border-l-stamp-blue',
    violet: 'border-l-stamp-blue',
    amber: 'border-l-stamp-amber',
  }[accent]
  return (
    <div className={`surface-card border-l-2 p-4 ${accentClass}`}>
      <p className="text-[11px] font-medium text-ink-muted">{label}</p>
      <p className="mt-2 font-mono text-2xl font-semibold tabular-nums text-ink">{value}</p>
      <p className="mt-1 truncate text-xs text-ink-muted">{detail}</p>
    </div>
  )
})

export function SummaryItem({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-[11px] text-ink-muted">{label}</p>
      <p className="mt-0.5 font-mono text-sm font-semibold tabular-nums text-ink">{value}</p>
    </div>
  )
}

export function SectionHeading({ eyebrow, title, detail }: { eyebrow: string; title: string; detail: string }) {
  return (
    <div className="flex items-end justify-between gap-4">
      <div>
        {/* 签名元素：一处 stamp-blue 短刻度，替代通用模板的「全大写 eyebrow」 */}
        <span aria-hidden="true" className="mb-2 block h-[3px] w-6 rounded-full bg-stamp-blue/70" />
        <p className="text-[11px] font-semibold text-stamp-blue">{eyebrow}</p>
        <h2 className="mt-1 font-serif text-lg font-semibold tracking-tight text-ink">{title}</h2>
      </div>
      <p className="text-xs text-ink-muted">{detail}</p>
    </div>
  )
}

export function EmptyState({ icon, title, text }: { icon: string; title: string; text: string }) {
  return (
    <div className="grid min-h-48 place-items-center text-center">
      <div className="max-w-xs">
        <div className="mx-auto grid h-11 w-11 place-items-center rounded-lg border border-rule bg-sheet text-lg text-ink-muted shadow-[0_1px_2px_rgba(23,27,25,0.05)]"
             aria-hidden="true">{icon}</div>
        <p className="mt-3 text-sm font-medium text-ink">{title}</p>
        <p className="mt-1 text-xs leading-5 text-ink-muted">{text}</p>
      </div>
    </div>
  )
}

export const TimelineItem = memo(function TimelineItem({ event }: { event: AguiEvent }) {
  const view = eventPresentation(event)
  return (
    <div className="group flex gap-3 rounded-lg px-2 py-3 animate-rise [content-visibility:auto] [contain-intrinsic-size:auto_72px]">
      <div className={`mt-0.5 grid h-7 w-7 shrink-0 place-items-center rounded-lg border text-xs ${view.iconClass}`} aria-hidden="true">{view.icon}</div>
      <div className="min-w-0 flex-1">
        <div className="flex items-center justify-between gap-3">
          <p className="truncate text-xs font-semibold text-ink">{view.title}</p>
          <span className="shrink-0 font-mono text-[10px] text-ink-muted">{event.type}</span>
        </div>
        <p className="mt-1 text-xs leading-5 text-ink-muted">{view.detail}</p>
      </div>
    </div>
  )
})

export const CitationCard = memo(function CitationCard({ citation, index }: { citation: CitationResult; index: number }) {
  const verified = citation.verified
  return (
    <article style={{ animationDelay: `${Math.min(index, 8) * 40}ms` }}
             className={`animate-rise rounded-lg border p-4 [content-visibility:auto] [contain-intrinsic-size:auto_200px] ${verified ? 'border-stamp-green/30 bg-stamp-green/[0.04]' : 'border-stamp-amber/30 bg-stamp-amber/[0.04]'}`}>
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <span className="font-mono text-[10px] text-ink-muted">#{index + 1}</span>
          <span className={`rounded-md px-2 py-1 text-[10px] font-semibold ${verified ? 'bg-stamp-green/10 text-stamp-green' : 'bg-stamp-amber/10 text-stamp-amber'}`}>
            {verified ? '严格通过' : citation.verified_relaxed ? '宽松通过' : '待复核'}
          </span>
        </div>
        <span className="font-mono text-xs text-ink-muted">{Math.round(citation.confidence * 100)}%</span>
      </div>
      <p className="mt-3 text-sm leading-6 text-ink">{citation.claim}</p>
      <div className="mt-3 border-t border-rule pt-3">
        <SourceLink source={citation.source} />
        {citation.note && <p className="mt-2 text-xs leading-5 text-ink-muted">{citation.note}</p>}
      </div>
    </article>
  )
})

export const SourceRow = memo(function SourceRow({ source, index }: { source: string; index: number }) {
  return (
    <div style={{ animationDelay: `${Math.min(index, 8) * 30}ms` }}
         className="surface-card-muted flex items-center gap-3 p-3 animate-rise [content-visibility:auto] [contain-intrinsic-size:auto_56px]">
      <span className="grid h-7 w-7 shrink-0 place-items-center rounded-lg bg-rule/40 font-mono text-[10px] text-ink-muted">{index + 1}</span>
      <div className="min-w-0 flex-1"><SourceLink source={source} /></div>
      <span className="rounded-md bg-rule/30 px-2 py-1 text-[10px] text-ink-muted">{sourceType(source)}</span>
    </div>
  )
})

export function SourceLink({ source }: { source: string }) {
  if (/^https?:\/\//i.test(source)) {
    return <a className="block truncate text-xs text-stamp-blue hover:text-stamp-green" href={source} target="_blank" rel="noreferrer">{source}</a>
  }
  return <p className="truncate font-mono text-xs text-ink-muted">{source}</p>
}
