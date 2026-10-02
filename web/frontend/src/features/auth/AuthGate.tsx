import Modal from '../../components/Modal'
import type { SessionUser } from '../../types/api'
import AuthForm from './AuthForm'

type Props = {
  /** 顶部提示（如「账号已注销」）。 */
  notice?: string
  /** 邀请链接预填的邀请码（带邀请链接打开时默认注册模式）。 */
  inviteFromUrl?: string
  onAuthed: (user: SessionUser) => void
  onOpenLegal: (doc: 'privacy' | 'terms') => void
}

type SheetProps = {
  title: string
  meta: string
  chips: string[]
  className: string
}

/** 背景「报告卡」（参考 LinkResume 的简历卡墙）：用产品自身的产物做装饰。
 *
 * 纯装饰：aria-hidden + pointer-events-none，不进入可访问树；内容为示意骨架线，
 * 不承诺任何真实数据。 */
function ReportSheet({ title, meta, chips, className }: SheetProps) {
  return (
    <div aria-hidden="true"
         className={`absolute rounded-xl border border-rule bg-sheet shadow-[0_18px_44px_-30px_rgba(23,27,25,0.55)] ${className}`}>
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
  )
}

const SHEETS: SheetProps[] = [
  {
    title: '生成式 AI 在企业知识管理中的落地路径',
    meta: '12 来源 · 30 论断 · 校验通过',
    chips: ['来源 3 · web', 'rag · 内规'],
    className: 'left-[2%] top-[13%] w-52 -rotate-[6deg] opacity-70 blur-[1.5px] hidden md:block',
  },
  {
    title: '2026 RAG 系统评估方法综述',
    meta: '9 来源 · 21 论断',
    chips: ['来源 7 · web', 'arxiv'],
    className: 'left-[6%] bottom-[5%] w-56 rotate-[3deg] opacity-85 blur-[0.5px] hidden sm:block',
  },
  {
    title: '国产大模型推理成本趋势（2026）',
    meta: '15 来源 · 44 论断',
    chips: ['来源 11 · web'],
    className: 'right-[3%] top-[9%] w-52 rotate-[6deg] opacity-70 blur-[1.5px] hidden md:block',
  },
  {
    title: 'Agent 工具生态与 MCP 协议观察',
    meta: '8 来源 · 17 论断',
    chips: ['来源 2 · code', 'web'],
    className: 'right-[5%] bottom-[7%] w-56 -rotate-[4deg] opacity-85 blur-[0.5px] hidden sm:block',
  },
  {
    title: '量子退火在物流调度中的应用',
    meta: '6 来源 · 12 论断',
    chips: ['来源 4 · web'],
    className: 'left-[21%] top-[3%] w-48 rotate-[2deg] opacity-50 blur-[2px] hidden lg:block',
  },
  {
    title: 'Transformer 在电力系统中的应用',
    meta: '10 来源 · 25 论断',
    chips: ['arxiv', '来源 6 · web'],
    className: 'right-[19%] bottom-[2%] w-48 -rotate-[3deg] opacity-50 blur-[2px] hidden lg:block',
  },
  // 移动端（<640px）：仅在角落保留两张淡化报告卡，避免背景空洞
  {
    title: '生成式 AI 在企业知识管理中的落地路径',
    meta: '12 来源 · 30 论断',
    chips: ['来源 3 · web'],
    className: 'sm:hidden left-[-16%] top-[4%] w-44 -rotate-[8deg] opacity-45 blur-[1.5px]',
  },
  {
    title: '2026 RAG 系统评估方法综述',
    meta: '9 来源 · 21 论断',
    chips: ['来源 7 · web', 'arxiv'],
    className: 'sm:hidden right-[-18%] bottom-[3%] w-44 rotate-[6deg] opacity-45 blur-[1.5px]',
  },
]

/** 登录门（档案墙）：背景铺斜排「报告卡」产物墙，居中 Hero + 表单卡。
 *
 * 构图参考 LinkResume（产品自身产物做装饰 + 居中大字 + 大留白），
 * 视觉语言保持「研究档案」（纸面/规则线/印章蓝/衬线主张）。
 * 复用 Modal 的焦点陷阱 / dialog 语义 / 滚动锁（不可关闭）。 */
export default function AuthGate({ notice, inviteFromUrl, onAuthed, onOpenLegal }: Props) {
  return (
    <Modal
      onClose={() => {}}
      labelledBy="auth-title"
      testId="auth-gate"
      dismissible={false}
      overlayClassName="z-50 overflow-y-auto bg-paper"
      panelClassName="relative flex min-h-full w-full flex-col"
    >
      {/* 背景：档案墙；中心径向留白把视觉焦点让给标题与表单 */}
      <div aria-hidden="true" className="pointer-events-none absolute inset-0 overflow-hidden">
        {SHEETS.map((sheet) => (
          <ReportSheet key={sheet.title} {...sheet} />
        ))}
        <div
          className="absolute inset-0"
          style={{
            background:
              'radial-gradient(ellipse 60% 52% at 50% 44%, rgba(244,246,243,0.97) 32%, rgba(244,246,243,0.78) 62%, rgba(244,246,243,0.30) 100%)',
          }}
        />
      </div>

      <header className="relative z-10 flex items-center justify-between gap-3 px-5 py-5 sm:px-10">
        <div className="flex items-center gap-2.5">
          <span aria-hidden="true" className="text-lg leading-none text-stamp-blue">◈</span>
          <div>
            <p className="text-sm font-semibold tracking-tight text-ink">DeepResearch</p>
            <p className="text-xs text-ink-muted">可审计研究工作台</p>
          </div>
        </div>
        <span className="rounded-full border border-rule bg-sheet/70 px-3 py-1 text-[11px] text-ink-muted">
          内测阶段，仅限邀请
        </span>
      </header>

      <main className="relative z-10 flex flex-1 flex-col items-center justify-center px-5 pb-14 pt-6 sm:px-10">
        <h1 className="archive-serif max-w-2xl text-center text-[27px] font-semibold leading-[1.35] text-ink sm:text-[40px] sm:leading-[1.28]">
          {'把复杂问题'}
          <br className="sm:hidden" />
          {'变成可追溯的研究结论'}
        </h1>
        <p className="mt-4 max-w-xl text-center text-[13px] leading-relaxed text-ink-muted sm:text-sm">
          从一次提问开始，追踪每一条来源、每一条论断与每一次修订
        </p>

        <div className="surface-card mt-9 w-full max-w-[400px] p-6 sm:p-7">
          <AuthForm
            idPrefix="auth"
            titleId="auth-title"
            initialMode={inviteFromUrl ? 'register' : 'login'}
            inviteFromUrl={inviteFromUrl}
            notice={notice}
            onAuthed={onAuthed}
            onOpenLegal={onOpenLegal}
          />
        </div>
      </main>
    </Modal>
  )
}
