/** 会话作用域本地状态清理（审计 #328）。
 *
 * 为什么单独一个模块：
 * App 与 `useResearchStream` 各自持有会话级状态（表单草稿 / 上次请求参数 / 活跃 run），
 * 登出时靠调用方「逐处记得清」——上一轮就漏过：`resetLocalData` 只清了 AccountPanel 域，
 * App 级报告与在途 SSE 仍留在屏幕上，换账号登录后能看到上一账号的报告/证据，
 * 刷新后 `dr.draft` 还会把上一账号的主题填回表单。
 *
 * 因此把「登出必须清除的键」与「运行域空闲态」收敛成单一真源 + 纯函数：
 * 生产代码与单测引用同一份清单，新增会话级键时必须在这里登记。
 */

/** 最近一次运行 id（`sessionStorage`：按标签页隔离；终局后保留供刷新回看）。 */
export const ACTIVE_RUN_KEY = 'dr.lastRunId'

/** 研究表单草稿（`sessionStorage`：刷新/误关页面不丢正在写的研究主题）。 */
export const DRAFT_KEY = 'dr.draft'

/** 上次实际发起参数（`sessionStorage`：刷新后「同参数重试」仍可用）。 */
export const LAST_REQUEST_KEY = 'dr.lastRequest'

/** 登出/切换账号必须清除的 sessionStorage 键（顺序固定，便于审阅与断言）。 */
export const SIGNED_OUT_STORAGE_KEYS = [ACTIVE_RUN_KEY, DRAFT_KEY, LAST_REQUEST_KEY] as const

/** 与 Web Storage 解耦的最小写接口：生产传 `sessionStorage`，测试传 fake。 */
export type RemovableStorage = { removeItem(key: string): void }

/** 清除全部会话级键；Storage 不可用（隐私模式/配额异常）时静默，不阻断登出。 */
export function clearSignedOutStorage(storage: RemovableStorage | null | undefined): void {
  if (!storage) return
  for (const key of SIGNED_OUT_STORAGE_KEYS) {
    try {
      storage.removeItem(key)
    } catch {
      /* 登出流程不允许因 storage 异常而中断 */
    }
  }
}

/** 运行域「空闲态」：`resetRunState()` 把 hook 的全部运行状态恢复到这一份。
 *
 * 放在纯函数模块而不是 hook 内，是为了让「登出后 runId=null」这类断言可被单测覆盖
 * （仓库没有 jsdom/testing-library，hook 层无法直接挂载）。 */
export const IDLE_RUN_STATE = {
  runId: null,
  status: 'idle',
  connectionStatus: 'idle',
  error: null,
  result: null,
  reportPreview: '',
} as const
