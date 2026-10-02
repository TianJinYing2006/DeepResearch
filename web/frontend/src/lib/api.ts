/** 统一请求辅助（R2）：CSRF / 错误归一 —— 消灭组件与 hook 之间的三套重复实现。 */
import type { StructuredError } from '../types/agui'

/** 双提交 Cookie 的 CSRF 头（无 cookie 时返回空对象）。 */
export function csrfHeaders(): Record<string, string> {
  const match = document.cookie.match(/(?:^|;\s*)(?:__Host-)?dr_csrf=([^;]+)/)
  return match ? { 'X-CSRF-Token': decodeURIComponent(match[1]) } : {}
}

/** 把任意来源的错误归一成 `StructuredError`（P1-5）。
 *
 * 后端已统一返回 `{code, message, component, node, detail, retryable, hint}`；
 * 但网络中断、JSON 解析失败这类**前端侧**错误没有后端载荷 ⇒ 在这里补齐同构字段，
 * 让错误卡片只需处理一种形状，不必到处判断「这次有没有 code」。 */
export function toStructuredError(value: unknown, code: string, hint: string): StructuredError {
  const message = value instanceof Error ? value.message : typeof value === 'string' ? value : ''
  if (value && typeof value === 'object' && 'code' in value) {
    const parsed = value as Partial<StructuredError>
    return {
      code: parsed.code ?? code,
      message: parsed.message ?? message,
      component: parsed.component ?? null,
      node: parsed.node ?? null,
      detail: parsed.detail ?? null,
      retryable: parsed.retryable ?? false,
      hint: parsed.hint ?? hint,
    }
  }
  return {
    code,
    message: message || hint,
    component: null,
    node: null,
    detail: null,
    retryable: false,
    hint,
  }
}

/** HTTP 非 2xx → StructuredError（解析 FastAPI detail；对象 / 字符串都兼容）。 */
export async function httpError(response: Response, code: string, hint: string): Promise<StructuredError> {
  let body: { detail?: unknown } | null = null
  try {
    body = (await response.json()) as { detail?: unknown }
  } catch {
    body = null
  }
  const detail = body?.detail
  if (detail && typeof detail === 'object') {
    return toStructuredError(detail, code, hint)
  }
  return toStructuredError(
    typeof detail === 'string' ? new Error(detail) : new Error(`${hint}（HTTP ${response.status}）`),
    code,
    hint,
  )
}

/** 从错误响应体提取用户可读文案（无结构化载荷时回落 HTTP 状态 / 网络错误）。 */
export function errorMessageFromBody(body: unknown, status = 0): string {
  if (body && typeof body === 'object' && 'detail' in body) {
    const detail = (body as { detail?: unknown }).detail
    if (detail && typeof detail === 'object' && 'message' in detail) {
      return String((detail as { message?: string }).message ?? '')
    }
    if (typeof detail === 'string') return detail
  }
  return status ? `HTTP ${status}` : '网络错误，请重试'
}

/** 非 2xx Response → 用户可读文案。 */
export async function readErrorMessage(response: Response): Promise<string> {
  let body: unknown = null
  try {
    body = await response.json()
  } catch {
    body = null
  }
  return errorMessageFromBody(body, response.status)
}
