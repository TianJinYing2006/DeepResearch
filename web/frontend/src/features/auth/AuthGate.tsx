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

const PROMISES = ['来源可核验', '论断可追溯', '过程可复盘']

/** 登录门（档案借阅台）：桌面端左「主张 + 审计承诺」右「表单」，移动端收起为单列。
 *
 * 复用 Modal 的焦点陷阱 / dialog 语义 / 滚动锁（不可关闭），视觉上是整页纸面卡。 */
export default function AuthGate({ notice, inviteFromUrl, onAuthed, onOpenLegal }: Props) {
  return (
    <Modal
      onClose={() => {}}
      labelledBy="auth-title"
      testId="auth-gate"
      dismissible={false}
      overlayClassName="z-50 flex items-center justify-center overflow-y-auto bg-paper"
      panelClassName="surface-card w-full max-w-5xl overflow-hidden p-0"
    >
      <div className="grid lg:grid-cols-[minmax(0,5fr)_minmax(0,6fr)]">
        <aside className="hidden flex-col justify-between gap-12 border-r border-rule bg-paper p-10 lg:flex">
          <div className="flex items-center gap-2.5">
            <span aria-hidden="true" className="text-lg leading-none text-stamp-blue">◈</span>
            <div>
              <p className="text-sm font-semibold tracking-tight text-ink">DeepResearch</p>
              <p className="text-xs text-ink-muted">可审计研究工作台</p>
            </div>
          </div>

          <div>
            <h1 className="archive-serif text-[34px] font-semibold leading-[1.35] text-ink">
              把复杂问题
              <br />
              变成可追溯的研究结论
            </h1>
            <ul className="mt-10 text-[15px] text-ink-muted">
              {PROMISES.map((item) => (
                <li key={item} className="border-t border-rule py-3.5 first:border-t-0 first:pt-0">
                  {item}
                </li>
              ))}
            </ul>
          </div>

          <p className="text-xs text-ink-muted">内测阶段，仅限邀请</p>
        </aside>

        <div className="flex items-center p-6 sm:p-10">
          <div className="mx-auto w-full max-w-sm">
            <div className="mb-7 flex items-center gap-2.5 lg:hidden">
              <span aria-hidden="true" className="text-lg leading-none text-stamp-blue">◈</span>
              <div>
                <p className="text-sm font-semibold tracking-tight text-ink">DeepResearch</p>
                <p className="text-xs text-ink-muted">可审计研究工作台</p>
              </div>
            </div>
            <p className="archive-serif mb-7 text-[19px] leading-snug text-ink lg:hidden">
              把复杂问题变成可追溯的研究结论
            </p>
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
        </div>
      </div>
    </Modal>
  )
}
