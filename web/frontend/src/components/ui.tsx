/** 通用展示原语（R4a：从 App.tsx 迁出；纯展示，无业务状态）。 */
import { eventPresentation, sourceType } from '../lib/presentation'
import type { AguiEvent, CitationResult } from '../types/agui'

export function BoundaryItem({ title, text }: { title: string; text: string }) {
  return (
    <div className="flex gap-3">
      <span className="mt-1 h-1.5 w-1.5 shrink-0 rounded-full bg-emerald-400/70" aria-hidden="true" />
      <p><span className="font-medium text-slate-300">{title}：</span>{text}</p>
    </div>
  )
}

export function MetricCard({ label, value, detail, accent }: { label: string; value: string; detail: string; accent: 'emerald' | 'brand' | 'violet' | 'amber' }) {
  const accentClass = {
    emerald: 'from-emerald-400/20 text-emerald-200',
    brand: 'from-brand-400/20 text-brand-200',
    violet: 'from-violet-400/20 text-violet-200',
    amber: 'from-amber-400/20 text-amber-200',
  }[accent]
  return (
    <div className={`surface-card bg-gradient-to-br ${accentClass} to-transparent p-4`}>
      <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-400">{label}</p>
      <p className="mt-2 font-mono text-2xl font-semibold tabular-nums">{value}</p>
      <p className="mt-1 truncate text-xs text-slate-400">{detail}</p>
    </div>
  )
}

export function SummaryItem({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-[11px] text-slate-400">{label}</p>
      <p className="mt-0.5 font-mono text-sm font-semibold tabular-nums text-slate-200">{value}</p>
    </div>
  )
}

export function SectionHeading({ eyebrow, title, detail }: { eyebrow: string; title: string; detail: string }) {
  return (
    <div className="flex items-end justify-between gap-4">
      <div>
        <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-emerald-300/55">{eyebrow}</p>
        <h2 className="mt-1 text-lg font-semibold text-white">{title}</h2>
      </div>
      <p className="text-xs text-slate-400">{detail}</p>
    </div>
  )
}

export function EmptyState({ icon, title, text }: { icon: string; title: string; text: string }) {
  return (
    <div className="grid min-h-48 place-items-center text-center">
      <div className="max-w-xs">
        <div className="mx-auto grid h-11 w-11 place-items-center rounded-xl border border-white/10 bg-white/[0.03] text-slate-400" aria-hidden="true">{icon}</div>
        <p className="mt-3 text-sm font-medium text-slate-300">{title}</p>
        <p className="mt-1 text-xs leading-5 text-slate-400">{text}</p>
      </div>
    </div>
  )
}

export function TimelineItem({ event }: { event: AguiEvent }) {
  const view = eventPresentation(event)
  return (
    <div className="group flex gap-3 rounded-xl px-2 py-3">
      <div className={`mt-0.5 grid h-7 w-7 shrink-0 place-items-center rounded-lg border text-xs ${view.iconClass}`} aria-hidden="true">{view.icon}</div>
      <div className="min-w-0 flex-1">
        <div className="flex items-center justify-between gap-3">
          <p className="truncate text-xs font-semibold text-slate-300">{view.title}</p>
          <span className="shrink-0 font-mono text-[10px] text-slate-400">{event.type}</span>
        </div>
        <p className="mt-1 text-xs leading-5 text-slate-400">{view.detail}</p>
      </div>
    </div>
  )
}

export function CitationCard({ citation, index }: { citation: CitationResult; index: number }) {
  const verified = citation.verified
  return (
    <article className={`rounded-xl border p-4 ${verified ? 'border-emerald-300/15 bg-emerald-300/[0.04]' : 'border-amber-300/15 bg-amber-300/[0.04]'}`}>
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <span className="font-mono text-[10px] text-slate-400">#{index + 1}</span>
          <span className={`rounded-md px-2 py-1 text-[10px] font-semibold ${verified ? 'bg-emerald-300/10 text-emerald-200' : 'bg-amber-300/10 text-amber-200'}`}>
            {verified ? '严格通过' : citation.verified_relaxed ? '宽松通过' : '待复核'}
          </span>
        </div>
        <span className="font-mono text-xs text-slate-400">{Math.round(citation.confidence * 100)}%</span>
      </div>
      <p className="mt-3 text-sm leading-6 text-slate-300">{citation.claim}</p>
      <div className="mt-3 border-t border-white/[0.06] pt-3">
        <SourceLink source={citation.source} />
        {citation.note && <p className="mt-2 text-xs leading-5 text-slate-400">{citation.note}</p>}
      </div>
    </article>
  )
}

export function SourceRow({ source, index }: { source: string; index: number }) {
  return (
    <div className="surface-card-muted flex items-center gap-3 p-3">
      <span className="grid h-7 w-7 shrink-0 place-items-center rounded-lg bg-black/20 font-mono text-[10px] text-slate-400">{index + 1}</span>
      <div className="min-w-0 flex-1"><SourceLink source={source} /></div>
      <span className="rounded-md bg-white/[0.04] px-2 py-1 text-[10px] uppercase text-slate-400">{sourceType(source)}</span>
    </div>
  )
}

export function SourceLink({ source }: { source: string }) {
  if (/^https?:\/\//i.test(source)) {
    return <a className="block truncate text-xs text-emerald-300/80 hover:text-emerald-200" href={source} target="_blank" rel="noreferrer">{source}</a>
  }
  return <p className="truncate font-mono text-xs text-slate-400">{source}</p>
}
