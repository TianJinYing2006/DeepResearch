import { useEffect, useRef, type CSSProperties } from 'react'

type SheetProps = {
  title: string
  meta: string
  chips: string[]
  /** 定位/尺寸/淡出/响应式显隐（不含 rotate —— 交给漂浮关键帧）。 */
  className: string
  /** 漂浮基准角度（度）。 */
  rot: number
  /** 鼠标视差深度（0–1，越大跟随越明显）。 */
  depth: number
  /** 漂浮相位错峰（秒）。 */
  delay: number
  /** 漂浮周期（秒）。 */
  duration: number
}

/** 背景「报告卡」：用产品自身的产物做装饰（参考 LinkResume 的简历卡墙）。
 *
 * 纯装饰：aria-hidden + pointer-events-none，不进入可访问树；内容为示意骨架线，
 * 不承诺任何真实数据。外层做鼠标视差（由 `--px/--py` 驱动），内层做缓慢漂浮。 */
function ReportSheet({ title, meta, chips, className, rot, depth, delay, duration }: SheetProps) {
  const driftStyle = {
    '--drift-rot': `${rot}deg`,
    animationDelay: `${delay}s`,
    animationDuration: `${duration}s`,
  } as CSSProperties

  return (
    <div
      aria-hidden="true"
      className={`absolute ${className}`}
      style={{
        transform: `translate3d(calc(var(--px, 0px) * ${depth}), calc(var(--py, 0px) * ${depth}), 0)`,
      }}
    >
      <div className="card-drift" style={driftStyle}>
        <div className="rounded-xl border border-rule bg-sheet shadow-[0_18px_44px_-30px_rgba(23,27,25,0.55)]">
          <div className="border-b border-rule/80 px-4 py-3">
            <p className="font-serif text-[13px] font-semibold leading-snug text-ink">{title}</p>
            <p className="mt-1 text-[10px] text-ink-muted">{meta}</p>
          </div>
          <div className="space-y-2 px-4 py-3">
            <div className="h-1.5 w-11/12 rounded bg-rule/70" />
            <div className="h-1.5 w-4/5 rounded bg-rule/60" />
            <div className="h-1.5 w-2/3 rounded bg-stamp-blue/25" />
            <div className="h-1.5 w-5/6 rounded bg-rule/60" />
            <div className="h-1.5 w-3/5 rounded bg-rule/50" />
          </div>
          <div className="flex flex-wrap gap-1.5 px-4 pb-3.5">
            {chips.map((chip) => (
              <span key={chip}
                    className="rounded-full border border-rule px-2 py-0.5 text-[10px] text-ink-muted">
                {chip}
              </span>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}

const SHEETS: SheetProps[] = [
  {
    title: '生成式 AI 在企业知识管理中的落地路径',
    meta: '12 来源 · 30 论断 · 校验通过',
    chips: ['来源 3 · web', 'rag · 内规'],
    className: 'left-[2%] top-[13%] w-52 opacity-70 blur-[1.5px] hidden md:block',
    rot: -6,
    depth: 0.55,
    delay: 1.4,
    duration: 19,
  },
  {
    title: '2026 RAG 系统评估方法综述',
    meta: '9 来源 · 21 论断',
    chips: ['来源 7 · web', 'arxiv'],
    className: 'left-[6%] bottom-[5%] w-56 opacity-85 blur-[0.5px] hidden sm:block',
    rot: 3,
    depth: 0.35,
    delay: 0.4,
    duration: 22,
  },
  {
    title: '国产大模型推理成本趋势（2026）',
    meta: '15 来源 · 44 论断',
    chips: ['来源 11 · web'],
    className: 'right-[3%] top-[9%] w-52 opacity-70 blur-[1.5px] hidden md:block',
    rot: 6,
    depth: 0.5,
    delay: 2.2,
    duration: 20,
  },
  {
    title: 'Agent 工具生态与 MCP 协议观察',
    meta: '8 来源 · 17 论断',
    chips: ['来源 2 · code', 'web'],
    className: 'right-[5%] bottom-[7%] w-56 opacity-85 blur-[0.5px] hidden sm:block',
    rot: -4,
    depth: 0.3,
    delay: 1.0,
    duration: 21,
  },
  {
    title: '量子退火在物流调度中的应用',
    meta: '6 来源 · 12 论断',
    chips: ['来源 4 · web'],
    className: 'left-[21%] top-[3%] w-48 opacity-50 blur-[2px] hidden lg:block',
    rot: 2,
    depth: 0.7,
    delay: 1.8,
    duration: 24,
  },
  {
    title: 'Transformer 在电力系统中的应用',
    meta: '10 来源 · 25 论断',
    chips: ['arxiv', '来源 6 · web'],
    className: 'right-[19%] bottom-[2%] w-48 opacity-50 blur-[2px] hidden lg:block',
    rot: -3,
    depth: 0.65,
    delay: 0.7,
    duration: 23,
  },
  // 移动端（<640px）：仅在角落保留两张淡化报告卡，避免背景空洞
  {
    title: '生成式 AI 在企业知识管理中的落地路径',
    meta: '12 来源 · 30 论断',
    chips: ['来源 3 · web'],
    className: 'sm:hidden left-[-16%] top-[4%] w-44 opacity-45 blur-[1.5px]',
    rot: -8,
    depth: 0.4,
    delay: 0.6,
    duration: 20,
  },
  {
    title: '2026 RAG 系统评估方法综述',
    meta: '9 来源 · 21 论断',
    chips: ['来源 7 · web', 'arxiv'],
    className: 'sm:hidden right-[-18%] bottom-[3%] w-44 opacity-45 blur-[1.5px]',
    rot: 6,
    depth: 0.35,
    delay: 1.6,
    duration: 22,
  },
]

/** 档案墙背景：斜排「报告卡」+ 研究轨迹虚线 + 中心径向留白 + 鼠标视差。
 *
 * 供落地页 Hero 使用；纯装饰层（`aria-hidden`），不响应点击。 */
export default function ArchiveWall({ className = '' }: { className?: string }) {
  const ref = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return
    const node = ref.current
    if (!node) return
    let raf = 0
    let targetX = 0
    let targetY = 0
    let currentX = 0
    let currentY = 0
    const tick = () => {
      currentX += (targetX - currentX) * 0.06
      currentY += (targetY - currentY) * 0.06
      node.style.setProperty('--px', `${(currentX * 16).toFixed(2)}px`)
      node.style.setProperty('--py', `${(currentY * 12).toFixed(2)}px`)
      raf =
        Math.abs(targetX - currentX) > 0.002 || Math.abs(targetY - currentY) > 0.002
          ? requestAnimationFrame(tick)
          : 0
    }
    const onMove = (event: MouseEvent) => {
      targetX = (event.clientX / window.innerWidth - 0.5) * 2
      targetY = (event.clientY / window.innerHeight - 0.5) * 2
      if (!raf) raf = requestAnimationFrame(tick)
    }
    window.addEventListener('mousemove', onMove)
    return () => {
      window.removeEventListener('mousemove', onMove)
      if (raf) cancelAnimationFrame(raf)
    }
  }, [])

  return (
    <div ref={ref} aria-hidden="true"
         className={`pointer-events-none absolute inset-0 overflow-hidden ${className}`}>
      {SHEETS.map((sheet) => (
        <ReportSheet key={`${sheet.title}-${sheet.className}`} {...sheet} />
      ))}

      <svg className="absolute left-1/2 top-[44%] hidden h-[560px] w-[860px] -translate-x-1/2 -translate-y-1/2 sm:block"
           viewBox="0 0 860 560" fill="none">
        <ellipse cx="430" cy="280" rx="410" ry="255" stroke="#D9DEDA" strokeWidth="1" />
        <ellipse cx="430" cy="280" rx="410" ry="255" stroke="#1E5AD8" strokeOpacity="0.3"
                 strokeWidth="1.5" strokeDasharray="2 14" className="trace-dash" />
      </svg>

      <div
        className="absolute inset-0"
        style={{
          background:
            'radial-gradient(ellipse 60% 52% at 50% 44%, rgba(244,246,243,0.97) 32%, rgba(244,246,243,0.78) 62%, rgba(244,246,243,0.30) 100%)',
        }}
      />
    </div>
  )
}
