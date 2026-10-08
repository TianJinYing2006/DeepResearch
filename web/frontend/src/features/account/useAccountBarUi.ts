/** 账号工具条 UI 状态（验收点③ 切片 S2 · state）：错误条 / 历史 / 知识库 / 安全面板的开合。
 *
 * 为什么单独成文件：这 5 个状态只服务于账号条这一排按钮与四个面板的显隐，
 * 与账号会话、配额、知识库**数据**都没有关系 —— 它们原本和 20 多个别的 state 挤在一起。
 *
 * ⚠️ 三个不能想当然的点（均来自拆分前的风险清单）：
 *
 * **R6 · `barMessage` 是跨领域的错误通道。** 写点来自三个不同领域：
 * 会话（`logout` 失败）、知识库（`deleteDoc` 失败）、账号（`deleteAccount` 失败）；
 * 读点只有账号条那一行。所以它**不能**随知识库一起搬进 KB 切片 ——
 * 搬走会让「删文档失败」的提示从账号条消失。本切片把它留在这里是正确的归属。
 *
 * **R4 · `historyEpoch` 是登出清空历史的唯一机制。** `HistoryPanel` 只在 `open` 变化时重拉，
 * 故登出要靠 `key={historyEpoch}` 强制重挂载。它必须与 `clear()` 同进同出 ——
 * 若 epoch 留在这里而清理逻辑搬去别处，就会回归成「登出后重登看到上一账号的历史」。
 *
 * **R5 · setter 必须保持 `Dispatch<SetStateAction<T>>` 的完整签名。** 调用点存在
 * `setHistoryOpen((open) => !open)` 这类函数式更新；把参数类型收窄成 `boolean`
 * 会直接编译失败（本次拆分第一版就踩了，tsc 报 TS2345 ×3）。
 *
 * 另外 `historyOpen` 必须保持**受控属性**语义：`HistoryPanel` 是常驻挂载、用 `open` 控制显隐，
 * 一旦改成 `{historyOpen && <HistoryPanel/>}`，防抖 effect 与「关闭保留已加载数据」的行为都会变。
 */
import { useCallback, useState } from 'react'
import type { Dispatch, SetStateAction } from 'react'

export type AccountBarUi = {
  /** 账号条上的跨领域错误提示（会话 / 知识库 / 注销三条线共用，见 R6）。 */
  barMessage: string
  setBarMessage: Dispatch<SetStateAction<string>>
  /** 历史面板开合（受控属性，勿改成条件挂载，见文件头）。 */
  historyOpen: boolean
  setHistoryOpen: Dispatch<SetStateAction<boolean>>
  /** 历史面板的强制重挂载计数（登出清空的唯一机制，见 R4）。 */
  historyEpoch: number
  setHistoryEpoch: Dispatch<SetStateAction<number>>
  kbOpen: boolean
  setKbOpen: Dispatch<SetStateAction<boolean>>
  securityOpen: boolean
  setSecurityOpen: Dispatch<SetStateAction<boolean>>
  /** 登出/注销清理：错误条清空、四个面板收起、历史面板强制重挂载。 */
  clear: () => void
}

export function useAccountBarUi(): AccountBarUi {
  const [barMessage, setBarMessage] = useState('')
  const [historyOpen, setHistoryOpen] = useState(false)
  const [historyEpoch, setHistoryEpoch] = useState(0)
  const [kbOpen, setKbOpen] = useState(false)
  const [securityOpen, setSecurityOpen] = useState(false)

  const clear = useCallback(() => {
    setBarMessage('')
    setHistoryOpen(false)
    setKbOpen(false)
    setSecurityOpen(false)
    setHistoryEpoch((epoch) => epoch + 1)
  }, [])

  return {
    barMessage, setBarMessage,
    historyOpen, setHistoryOpen,
    historyEpoch, setHistoryEpoch,
    kbOpen, setKbOpen,
    securityOpen, setSecurityOpen,
    clear,
  }
}
