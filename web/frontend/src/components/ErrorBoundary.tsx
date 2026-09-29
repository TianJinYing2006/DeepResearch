import { Component, type ErrorInfo, type ReactNode } from 'react'

type Props = { children: ReactNode }
type State = { error: Error | null }

/**
 * R1（审计 U2/U32）：渲染异常兜底 —— 单个畸形事件 payload 不得让整页白屏。
 *
 * 只显示错误消息（不含用户内容），提供刷新入口；错误同时写入 console 便于排障。
 */
export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error('[ui] render error:', error, info.componentStack)
  }

  render() {
    if (this.state.error === null) return this.props.children
    return (
      <div className="flex min-h-dvh items-center justify-center p-6" data-testid="error-boundary">
        <div className="surface-card w-full max-w-md p-6">
          <h1 className="text-lg font-semibold text-ink">页面渲染出错</h1>
          <p className="mt-2 text-sm leading-6 text-ink-muted">
            界面遇到未预期的数据，已阻止白屏。可以先刷新重试；若反复出现，请反馈下方错误信息。
          </p>
          <pre className="mt-3 max-h-32 overflow-auto rounded-lg border border-rule bg-rule/40 p-3 text-[11px] leading-5 text-ink-muted">
            {String(this.state.error.message || this.state.error)}
          </pre>
          <button type="button" className="primary-button mt-4 w-full"
                  onClick={() => window.location.reload()}>
            刷新页面
          </button>
        </div>
      </div>
    )
  }
}
