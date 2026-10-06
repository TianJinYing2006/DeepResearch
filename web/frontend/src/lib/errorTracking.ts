/** 需求 25：错误追踪（Sentry 协议；动态加载 —— DSN 空 = 不加载、零网络请求）。
 *
 * - DSN 由 `/api/options` 运行时下发（同一构建产物可跑任何环境）；
 * - PII 最小化：sendDefaultPii=false、不做性能追踪；
 * - 观测旁路：任何失败静默，不阻断主流程。
 */
let initialized = false

export async function initErrorTracking(options: {
  dsn?: string | null
  environment?: string | null
  release?: string | null
}): Promise<void> {
  if (initialized || !options.dsn) return
  try {
    const Sentry = await import('@sentry/browser')
    Sentry.init({
      dsn: options.dsn,
      environment: options.environment || undefined,
      release: options.release || undefined,
      tracesSampleRate: 0,
      // PII 最小化（v11 的 dataCollection；等价旧版 sendDefaultPii=false）：
      // 不采用户信息/Cookie/请求体/URL 查询参数/栈帧局部变量；敏感头由 SDK 强制过滤
      dataCollection: {
        userInfo: false,
        cookies: false,
        httpBodies: [],
        urlQueryParams: false,
        stackFrameVariables: false,
      },
    })
    initialized = true
  } catch {
    /* 观测旁路不阻断 */
  }
}

export async function captureException(error: unknown): Promise<void> {
  if (!initialized) return
  try {
    const Sentry = await import('@sentry/browser')
    Sentry.captureException(error)
  } catch {
    /* noop */
  }
}
