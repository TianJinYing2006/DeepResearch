/** 会话入口状态与首帧分流（验收点③ 切片 S1 · 会话域）。
 *
 * 覆盖两件事：**谁登录了**（`user` / `checked` / `loadSession`）、**未登录访客看哪一屏**
 * （`authView`）。
 *
 * ⚠️ 三条不能想当然的点（来自拆分前的风险清单，后续切片请照看）：
 *
 * **R1 · `checked` 不能被 `user` 取代。** 三者语义不同：`checked=false` 是「还没问过后端」，
 * `checked=true && user=null` 是「问过了，确实未登录」。若用 `user === null` 兼作加载态，
 * 首帧会闪一下登录门，且匿名可用模式（`authRequired=false`）下历史/上传入口会被误挡。
 *
 * **R2 · `loadSession` 用 `setChecked(true)` 标记「问完了」，且失败路径必须同样落值。**
 * try/catch 之后那段 `setUser(null); setChecked(true)` 不是冗余 —— 网络失败要按**未登录**处理
 * 而不是永远停在加载态。这是故障透明需求的一部分，别为了「少一次渲染」把它挪进 catch。
 *
 * **R4 · `authView` 的初始值分支顺序有意义**：`#reset=` 重置链接 / `?login` 命中直达登录页，
 * 否则才是落地页。`try/catch` 是给 `URLSearchParams` 在异常环境下兜底（返回 `'landing'`），
 * 删掉会让整个面板白屏。
 *
 * 另外：本文件**只搬状态与 `loadSession`，不搬任何 JSX**。
 */
import { useCallback, useState } from 'react'
import type { Dispatch, SetStateAction } from 'react'
import type { SessionUser } from '../../types/api'

export type SessionEntry = {
  /** 当前登录用户；`null` 表示未登录（需配合 `checked` 判断是「未登录」还是「还没问」）。 */
  user: SessionUser | null
  setUser: Dispatch<SetStateAction<SessionUser | null>>
  /** 是否已完成一次会话探测（见 R1：不可用 `user === null` 代替）。 */
  checked: boolean
  setChecked: Dispatch<SetStateAction<boolean>>
  /** 登录/注册表单上的提示文案，也用于登出后的「已退出」回执。 */
  authNotice: string
  setAuthNotice: Dispatch<SetStateAction<string>>
  /** 未登录访客的分流视图，见 R4。 */
  authView: 'landing' | 'auth'
  setAuthView: Dispatch<SetStateAction<'landing' | 'auth'>>
  /** 拉取 `/api/auth/session`；失败按未登录处理（见 R2）。 */
  loadSession: () => Promise<void>
}

export function useSessionEntry(): SessionEntry {
  const [user, setUser] = useState<SessionUser | null>(null)
  const [checked, setChecked] = useState(false)
  const [authNotice, setAuthNotice] = useState('')

  // 未登录访客的分流视图：落地页（默认）→ 登录注册页；重置链接 / `?login` 直达登录页。
  const [authView, setAuthView] = useState<'landing' | 'auth'>(() => {
    try {
      if (window.location.hash.startsWith('#reset=')) return 'auth'
      return new URLSearchParams(window.location.search).has('login') ? 'auth' : 'landing'
    } catch {
      return 'landing'
    }
  })

  const loadSession = useCallback(async () => {
    try {
      const response = await fetch('/api/auth/session')
      if (response.ok) {
        const body = (await response.json()) as { user: SessionUser }
        setUser(body.user)
        setChecked(true)
        return
      }
    } catch {
      /* 网络失败按未登录处理（R2：下面两行不可省） */
    }
    setUser(null)
    setChecked(true)
  }, [])

  return {
    user, setUser,
    checked, setChecked,
    authNotice, setAuthNotice,
    authView, setAuthView,
    loadSession,
  }
}
