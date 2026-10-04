import { useEffect, useRef, useState, type CSSProperties } from 'react'
import { createPortal } from 'react-dom'
import { usePrefersReducedMotion, useRevealAll, useScrollState, useTypewriter } from '../../lib/useScrollFx'
import ArchiveWall from './ArchiveWall'

type Props = {
  /** 进入登录 / 注册（分流主 CTA）。 */
  onStart: () => void
  onOpenLegal: (doc: 'privacy' | 'terms') => void
  /** 需求 25：帮助中心（FAQ）弹窗。 */
  onOpenHelp: () => void
}

const MARQUEE_TOKENS = [
  '规划问题',
  '多跳检索',
  '引用校验',
  '可审计报告',
  '知识库检索',
  '运行档位与预算',
  'Markdown / JSON 导出',
  '来源可核验',
  '论断可追溯',
  '过程可复盘',
]

const WORKFLOW = [
  { title: '规划问题', copy: '拆解子问题，明确检索方向、跳数预算与运行档位。' },
  { title: '多跳检索', copy: '网页、知识库、arXiv 与代码执行，四类来源并行取证。' },
  { title: '撰写报告', copy: '每个论断携带来源编号，按章节组织成可阅读的长文。' },
  { title: '引用校验', copy: '存在性与忠实度双口径逐条核对，未通过的论断进入附录。' },
  { title: '渲染审计', copy: '类型标注、校验统计、运行溯源与治理事件一并呈现。' },
]

const FEATURES = [
  {
    title: '多跳检索',
    copy: '网页 / 知识库 / arXiv / 代码执行四类来源，并行取证、按跳数收敛。',
  },
  {
    title: '引用双口径校验',
    copy: '存在性与忠实度分别统计；未通过的论断显式列出原因，不混入正文。',
  },
  {
    title: '可审计报告',
    copy: '来源类型标注、校验统计、运行溯源与规划治理事件，随报告一并交付。',
  },
  {
    title: '知识库检索',
    copy: '把团队文档纳入检索（本地向量库），与网页来源同口径校验。',
  },
  {
    title: '运行档位',
    copy: '快 / 标准两档，跳数、字数与预算可控；超限降级全部留痕。',
  },
  {
    title: '随时导出',
    copy: 'Markdown / JSON 一键导出，含审计元数据，文件名即话题名。',
  },
]

/** 落地页（未登录访客）：产品介绍 + 随滚动变化的动效（reveal / sticky 工作流 / 进度条 / marquee）。
 *
 * 分流：本页 CTA 进入登录注册页；已登录用户不会看到本页（直接进工作台）。
 * 独立 overlay 滚动容器（fixed），动效全部尊重 `prefers-reduced-motion`。 */
export default function LandingPage({ onStart, onOpenLegal, onOpenHelp }: Props) {
  const scrollerRef = useRef<HTMLDivElement | null>(null)
  const contentRef = useRef<HTMLDivElement | null>(null)
  const stepRefs = useRef<Array<HTMLLIElement | null>>([])
  const [activeStep, setActiveStep] = useState(0)
  const reduced = usePrefersReducedMotion()
  const typedTitle = useTypewriter('把复杂问题变成可追溯的研究结论', 90, 300)

  useRevealAll(contentRef, scrollerRef)
  const { y, progress } = useScrollState(scrollerRef)

  // 工作流：滚动经过的步骤逐级激活（IO 中带窗口，跨过中线即激活）
  useEffect(() => {
    const scroller = scrollerRef.current
    if (!scroller) return
    const steps = stepRefs.current.filter((step): step is HTMLLIElement => Boolean(step))
    if (steps.length === 0) return
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (!entry.isIntersecting) return
          const index = steps.indexOf(entry.target as HTMLLIElement)
          if (index >= 0) setActiveStep(index)
        })
      },
      { root: scroller, rootMargin: '-45% 0px -45% 0px', threshold: 0 },
    )
    steps.forEach((step) => observer.observe(step))
    return () => observer.disconnect()
  }, [])

  const heroStyle = reduced
    ? undefined
    : { transform: `translateY(${(y * 0.16).toFixed(1)}px)`, opacity: Math.max(0, 1 - y / 560) }

  return createPortal(
    <div ref={scrollerRef} className="fixed inset-0 z-50 overflow-y-auto bg-paper" data-testid="landing-page">
      {/* 顶部滚动进度 */}
      <div className="pointer-events-none fixed left-0 top-0 z-[60] h-0.5 bg-stamp-blue transition-[width] duration-150"
           style={{ width: `${(progress * 100).toFixed(2)}%` }} data-testid="landing-progress" />

      <div ref={contentRef}>
        <header className="sticky top-0 z-30 border-b border-rule/70 bg-paper">
          <div className="mx-auto flex max-w-6xl items-center justify-between gap-3 px-5 py-3.5 sm:px-8">
            <div className="flex items-center gap-2.5">
              <span aria-hidden="true" className="text-lg leading-none text-stamp-blue">◈</span>
              <div>
                <p className="text-sm font-semibold tracking-tight text-ink">DeepResearch</p>
                <p className="text-xs text-ink-muted">可审计研究工作台</p>
              </div>
            </div>
            <div className="flex items-center gap-3">
              <span className="hidden rounded-full border border-rule px-3 py-1 text-[11px] text-ink-muted sm:inline-block">
                内测阶段，仅限邀请
              </span>
              <button type="button" data-testid="landing-nav-cta" onClick={onStart}
                      className="rounded-full bg-ink px-4 py-2 text-xs font-semibold text-white transition hover:-translate-y-px hover:bg-ink/90">
                登录 / 开始使用
              </button>
            </div>
          </div>
        </header>

        {/* Hero：档案墙 + 主张 + 主 CTA */}
        <section className="relative overflow-hidden">
          <ArchiveWall />
          <div className="relative mx-auto flex max-w-3xl flex-col items-center px-5 pb-24 pt-20 text-center sm:pb-32 sm:pt-28"
               style={heroStyle}>
            <h1 aria-label="把复杂问题变成可追溯的研究结论"
                className="archive-serif min-h-[2.75em] max-w-4xl text-center text-[30px] font-semibold leading-[1.32] text-ink sm:min-h-[1.32em] sm:text-[38px] sm:leading-[1.28] lg:text-[46px]">
              <span aria-hidden="true">{typedTitle}</span>
              <span aria-hidden="true"
                    className="animate-caret ml-1.5 inline-block h-[0.8em] w-[3px] translate-y-[0.08em] bg-ink align-baseline" />
            </h1>
            <p className="mt-5 max-w-xl text-center text-[13px] leading-relaxed text-ink-muted sm:text-[15px]">
              从一次提问开始，追踪每一条来源、每一条论断与每一次修订
            </p>
            <div className="mt-8 flex flex-col items-center gap-3">
              <button type="button" data-testid="landing-cta" onClick={onStart}
                      className="rounded-full bg-ink px-7 py-3.5 text-sm font-semibold text-white transition hover:-translate-y-px hover:bg-ink/90">
                开始使用
              </button>
              <a href="#workflow" className="text-xs text-ink-muted underline decoration-rule underline-offset-4 hover:text-ink">
                看它如何工作 ↓
              </a>
            </div>
          </div>
        </section>

        {/* Marquee：能力关键词横条（无缝循环，装饰） */}
        <section aria-hidden="true" data-testid="landing-marquee"
                 className="overflow-hidden border-y border-rule/70 bg-sheet/60 py-3.5">
          <div className="animate-marquee flex w-max items-center gap-10 whitespace-nowrap text-xs text-ink-muted">
            {[...MARQUEE_TOKENS, ...MARQUEE_TOKENS].map((token, index) => (
              <span key={`${token}-${index}`} className="flex items-center gap-10">
                <span>{token}</span>
                <span className="text-rule">◇</span>
              </span>
            ))}
          </div>
        </section>

        {/* 引言：一次提问，只是开始 */}
        <section className="mx-auto max-w-6xl px-5 py-20 sm:px-8 sm:py-24">
          <h2 className="archive-serif reveal text-2xl font-semibold text-ink sm:text-[32px]">
            一次提问，只是开始
          </h2>
          <p className="reveal mt-4 max-w-2xl text-sm leading-relaxed text-ink-muted" style={{ '--reveal-delay': '80ms' } as CSSProperties}>
            报告不该是黑盒。每一次运行都会留下可复核的材料：命中的来源、每一条论断的校验结论、
            以及过程中发生过的降级与治理事件。
          </p>
          <div className="mt-10 grid gap-4 sm:grid-cols-3">
            {[
              { title: '命中的来源', copy: '每条来源带类型标注（web / rag / arxiv / code），去重后计入运行溯源。' },
              { title: '每一条论断', copy: '报告中的引用编号可回溯到原文；未通过校验的论断进入显式附录。' },
              { title: '运行的过程', copy: '节点进度、跳数、Token 与成本、降级与恢复，全部随报告交付。' },
            ].map((card, index) => (
              <article key={card.title} className="surface-card reveal p-5"
                       style={{ '--reveal-delay': `${index * 90}ms` } as CSSProperties}>
                <h3 className="font-serif text-[15px] font-semibold text-ink">{card.title}</h3>
                <p className="mt-2 text-[13px] leading-relaxed text-ink-muted">{card.copy}</p>
              </article>
            ))}
          </div>
        </section>

        {/* 工作流：sticky 标题 + 滚动逐级激活的五步 */}
        <section id="workflow" className="border-t border-rule/70 bg-sheet/50">
          <div className="mx-auto grid max-w-6xl gap-10 px-5 py-20 sm:px-8 lg:grid-cols-[minmax(0,4fr)_minmax(0,6fr)]">
            <div className="lg:sticky lg:top-24 lg:self-start">
              <h2 className="archive-serif reveal text-2xl font-semibold text-ink sm:text-[32px]">
                从问题到报告，五步留痕
              </h2>
              <p className="reveal mt-4 text-sm leading-relaxed text-ink-muted" style={{ '--reveal-delay': '80ms' } as CSSProperties}>
                每个节点都会留下可复核的事件与计数；这就是「可审计」的含义。
              </p>
              <div className="reveal mt-8 h-1 w-full overflow-hidden rounded bg-rule/50" style={{ '--reveal-delay': '140ms' } as CSSProperties}>
                <div className="h-full rounded bg-stamp-blue transition-[width] duration-500"
                     style={{ width: `${((activeStep + 1) / WORKFLOW.length) * 100}%` }}
                     data-testid="workflow-progress" />
              </div>
            </div>

            <ol className="space-y-4">
              {WORKFLOW.map((step, index) => (
                <li key={step.title}
                    ref={(node) => { stepRefs.current[index] = node }}
                    data-testid="workflow-step"
                    data-active={activeStep === index}
                    className={`surface-card reveal p-5 ${
                      activeStep === index
                        ? 'border-stamp-blue/50 shadow-[0_1px_2px_rgba(30,90,216,0.10),0_18px_40px_-26px_rgba(30,90,216,0.45)]'
                        : 'opacity-70'
                    }`}
                    style={{ '--reveal-delay': `${index * 60}ms` } as CSSProperties}>
                  <div className="flex items-baseline gap-3">
                    <span className="archive-serif text-lg font-semibold text-ink-muted tabular-nums">
                      {String(index + 1).padStart(2, '0')}
                    </span>
                    <h3 className="text-sm font-semibold text-ink">{step.title}</h3>
                  </div>
                  <p className="mt-2 text-[13px] leading-relaxed text-ink-muted">{step.copy}</p>
                </li>
              ))}
            </ol>
          </div>
        </section>

        {/* 能力网格 */}
        <section className="mx-auto max-w-6xl px-5 py-20 sm:px-8 sm:py-24">
          <h2 className="archive-serif reveal text-2xl font-semibold text-ink sm:text-[32px]">
            为每一次研究，准备完整的工具
          </h2>
          <p className="reveal mt-4 max-w-2xl text-sm leading-relaxed text-ink-muted" style={{ '--reveal-delay': '80ms' } as CSSProperties}>
            从检索到交付，每一步都为「可信」服务。
          </p>
          <div className="mt-10 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {FEATURES.map((feature, index) => (
              <article key={feature.title} className="surface-card reveal p-5"
                       style={{ '--reveal-delay': `${(index % 3) * 90}ms` } as CSSProperties}>
                <h3 className="font-serif text-[15px] font-semibold text-ink">{feature.title}</h3>
                <p className="mt-2 text-[13px] leading-relaxed text-ink-muted">{feature.copy}</p>
              </article>
            ))}
          </div>
        </section>

        {/* 收尾 CTA（墨色） */}
        <section className="bg-ink text-white">
          <div className="mx-auto max-w-3xl px-5 py-24 text-center sm:px-8">
            <h2 className="archive-serif reveal text-2xl font-semibold leading-snug sm:text-[36px]">
              现在，把问题交给可审计的研究流程
            </h2>
            <p className="reveal mt-4 text-sm text-white/70" style={{ '--reveal-delay': '80ms' } as CSSProperties}>
              内测阶段，仅限邀请；需要邀请码注册。
            </p>
            <button type="button" data-testid="landing-cta-bottom" onClick={onStart}
                    className="reveal mt-8 rounded-full bg-white px-7 py-3.5 text-sm font-semibold text-ink transition hover:-translate-y-px hover:bg-white/90"
                    style={{ '--reveal-delay': '140ms' } as CSSProperties}>
              开始使用
            </button>
          </div>
        </section>

        <footer className="border-t border-rule/70 bg-paper">
          <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-3 px-5 py-6 text-[11px] text-ink-muted sm:px-8">
            <span>DeepResearch · 可审计研究工作台 · 内测阶段</span>
            <span className="flex gap-4">
              <button type="button" className="underline decoration-rule underline-offset-4 hover:text-ink"
                      data-testid="footer-help" onClick={onOpenHelp}>帮助中心</button>
              <button type="button" className="underline decoration-rule underline-offset-4 hover:text-ink"
                      onClick={() => onOpenLegal('terms')}>用户协议</button>
              <button type="button" className="underline decoration-rule underline-offset-4 hover:text-ink"
                      onClick={() => onOpenLegal('privacy')}>隐私政策</button>
            </span>
          </div>
        </footer>
      </div>
    </div>,
    document.body,
  )
}
