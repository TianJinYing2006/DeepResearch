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
  /** 返回落地页（存在落地页时提供）。 */
  onBack?: () => void
}

/** 登录注册页（v3，参考 LinkResume /login 的黑白分栏）：
 *
 * 左（移动端在上）：白卡表单；右（移动端在下）：墨色宣言板——
 * 衬线大字 + 闪烁光标 + 底部注解。保留全部 e2e 契约（AuthForm 未动）。 */
export default function AuthGate({ notice, inviteFromUrl, onAuthed, onOpenLegal, onBack }: Props) {
  return (
    <Modal
      onClose={() => {}}
      labelledBy="auth-title"
      testId="auth-gate"
      dismissible={false}
      overlayClassName="z-50 flex items-center justify-center overflow-y-auto bg-paper"
      panelClassName="surface-card w-full max-w-4xl overflow-hidden p-0"
    >
      <div className="grid lg:grid-cols-[minmax(0,58fr)_minmax(0,42fr)]">
        <div className="p-6 sm:p-9">
          <div className="mb-7 flex items-center justify-between gap-3">
            <div className="flex items-center gap-2.5">
              <span aria-hidden="true" className="text-lg leading-none text-stamp-blue">◈</span>
              <div>
                <p className="text-sm font-semibold tracking-tight text-ink">DeepResearch</p>
                <p className="text-xs text-ink-muted">可审计研究工作台</p>
              </div>
            </div>
            {onBack && (
              <button type="button" data-testid="auth-back" onClick={onBack}
                      className="text-xs text-ink-muted transition hover:text-ink">
                ← 返回介绍
              </button>
            )}
          </div>
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

        <aside className="flex flex-col justify-between gap-8 bg-ink p-7 text-white sm:p-9">
          <p className="archive-serif text-[24px] font-semibold leading-[1.4] sm:text-[28px]">
            {'把复杂问题，'}
            <br />
            {'变成可追溯的研究结论'}
            <span aria-hidden="true"
                  className="animate-caret ml-1.5 inline-block h-[0.75em] w-[3px] translate-y-[0.08em] bg-white align-baseline" />
          </p>
          <p className="text-xs leading-relaxed text-white/60">
            内测阶段，仅限邀请；普通账号由邀请码创建。
            登录后可在「会话与安全」中随时查看设备并退出其他会话。
          </p>
        </aside>
      </div>
    </Modal>
  )
}
