/** 认证相关 URL 解析（审计 #341b / F11）。
 *
 * 纯函数：不读写 DOM，便于单测（AuthForm 负责读取与清除地址栏）。
 */

/** 一次性密码重置链接的 hash 形态：`#reset=<10..256 位 urlsafe token>`。 */
export const RESET_HASH_RE = /^#reset=([A-Za-z0-9_-]{10,256})$/

/** 从 `location.hash` 提取重置 token；不合法 / 非重置链接返回 null。 */
export function extractResetToken(hash: string): string | null {
  const match = hash.match(RESET_HASH_RE)
  return match ? match[1] : null
}
