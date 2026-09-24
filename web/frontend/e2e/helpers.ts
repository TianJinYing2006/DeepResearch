import type { Page } from '@playwright/test'

const TERMINAL = /研究完成|已取消|已到时限停止|运行失败|等待任务/

/** 等页面回到终局态；还在跑就点「停止」。

 为什么必须有这一步：后端并发闸是 **1**（P1-3，前台模型一次只跑一个）。
 若某条用例结束时研究仍在运行，下一条用例发起时会被 429 拒掉 ——
 于是失败会**传染**，看起来像一堆用例同时挂，实际只有一个真因。

 ⚠️ 三条坑（都踩过）：
 1. 停止按钮在 `stopping` 态是 disabled ⇒ 必须判 `isEnabled()` 再点，
    否则 Playwright 会一直等到 action timeout（默认 30s/次），把清理拖成分钟级；
 2. 页面从未导航（如只打 API 的用例）时没有状态徽标 ⇒ 直接返回，不要空转；
 3. 任何异常都必须吞掉 —— 清理钩子失败会把**通过了的**用例判成失败。 */
export async function settleRun(page: Page, timeoutMs = 20_000): Promise<void> {
  const badge = page.locator('[data-testid="status-badge"]')
  const deadline = Date.now() + timeoutMs

  while (Date.now() < deadline) {
    try {
      if (page.isClosed()) return
      if ((await badge.count()) === 0) return
      const label = (await badge.textContent({ timeout: 1_000 })) ?? ''
      if (TERMINAL.test(label)) return

      const stop = page.locator('button:has-text("停止研究")')
      if ((await stop.count()) > 0 && (await stop.first().isEnabled())) {
        await stop.first().click({ timeout: 2_000 }).catch(() => {})
      }
    } catch {
      return
    }
    await page.waitForTimeout(400)
  }
}
