/** 跨标签页会话联动（审计 #341b / F6）。
 *
 * 场景：同一浏览器两个标签页登录同一账号；标签 A 退出/注销后，标签 B 的界面仍显示
 * 已登录，后续请求 401 却仍展示旧页面。做法：登出成功方写一个 localStorage 标记
 * （`storage` 事件只在**其它**标签页触发），其它标签页收到后重新探测
 * `/api/auth/session`，发现已登出即清理本地状态。
 *
 * 隐私边界：只写时间戳，不写 token / 用户信息。
 */

/** 会话变更广播键（值 = 毫秒时间戳，仅用于触发 `storage` 事件）。 */
export const SESSION_EPOCH_KEY = 'dr.sessionEpoch'

/** 与 Web Storage 解耦的最小写接口：生产传 `localStorage`，测试传 fake。 */
export type WritableStorage = { setItem(key: string, value: string): void }

/** 写会话变更标记；storage 不可用（隐私模式）时静默（跨标签页联动降级为不生效）。 */
export function notifySessionChanged(
  storage: WritableStorage | null | undefined,
  now: number = Date.now(),
): void {
  if (!storage) return
  try {
    storage.setItem(SESSION_EPOCH_KEY, String(now))
  } catch {
    /* 隐私模式/配额异常：不影响本标签页登出 */
  }
}

/** `storage` 事件是否来自会话变更标记（监听方只处理该 key）。 */
export function isSessionEpochEvent(key: string | null): boolean {
  return key === SESSION_EPOCH_KEY
}
