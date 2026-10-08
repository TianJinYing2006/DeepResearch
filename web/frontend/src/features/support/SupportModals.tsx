/** 支撑弹窗域（验收点③ 切片 S10）：帮助 / 反馈 / 分享管理。
 *
 * 为什么单独成文件：这三个弹窗在 AccountPanel 里只占 3 个布尔量与 3 行挂载，
 * 却和 29 个别的 state 挤在同一处；它们与账号会话、知识库、运行状态毫无耦合。
 *
 * 设计取舍（与 S5/S6/S7 一致）：hook **连 setter 一起返回并沿用原名**，
 * 调用方零改动。注意 AccountPanel 里有**两处** HelpModal 挂载 ——
 * 落地页分支（访客未登录）单独用 `helpOpen`/`setHelpOpen`，本组件只负责主分支那三行。
 */
import { useCallback, useState } from 'react'

import FeedbackModal from '../../components/FeedbackModal'
import HelpModal from '../../components/HelpModal'
import ShareManageModal from '../../components/ShareManageModal'

export type SupportModalController = {
  helpOpen: boolean
  setHelpOpen: (open: boolean) => void
  feedbackOpen: boolean
  setFeedbackOpen: (open: boolean) => void
  shareManageOpen: boolean
  setShareManageOpen: (open: boolean) => void
  /** 登出/注销清理：三扇门一并关掉。 */
  clear: () => void
}

export function useSupportModals(): SupportModalController {
  const [helpOpen, setHelpOpen] = useState(false)
  const [feedbackOpen, setFeedbackOpen] = useState(false)
  const [shareManageOpen, setShareManageOpen] = useState(false)

  const clear = useCallback(() => {
    setHelpOpen(false)
    setFeedbackOpen(false)
    setShareManageOpen(false)
  }, [])

  return { helpOpen, setHelpOpen, feedbackOpen, setFeedbackOpen, shareManageOpen, setShareManageOpen, clear }
}

/** 主分支的三个支撑弹窗。条件渲染语义与原来逐行一致（各自 `xxx && <Modal/>`），
 *  不合并成一个受控弹窗 —— 三者互不排斥，理论上可同时打开（模块级 Modal 栈会各自入栈）。 */
export function SupportModals({ support }: { support: SupportModalController }) {
  return (
    <>
      {support.helpOpen && <HelpModal onClose={() => support.setHelpOpen(false)} />}
      {support.feedbackOpen && <FeedbackModal onClose={() => support.setFeedbackOpen(false)} />}
      {support.shareManageOpen && <ShareManageModal onClose={() => support.setShareManageOpen(false)} />}
    </>
  )
}
