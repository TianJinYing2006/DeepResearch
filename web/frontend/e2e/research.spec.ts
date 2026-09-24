import { expect, test } from '@playwright/test'

import { settleRun } from './helpers'

/** 桌面端主流程：发起 → 实时事件 → 报告 → 导出。

 用 `DR_DEMO=1` 的假图（无 LLM、无网络），但**走真实的 SSE 管线**，
 因此断言的是「前端真的消费了后端事件」，不是静态页面快照。 */
test.describe('研究主流程（桌面端）', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/')
  })

  test.afterEach(async ({ page }) => {
    await settleRun(page)
  })

  test('发起研究后能看到实时进度与最终报告', async ({ page }) => {
    await page.fill('#topic', '端到端演示主题')

    await Promise.all([
      // 后端是前台模型：点提交后立刻进入 running，状态徽标先变，再等报告
      expect(page.getByText('研究进行中')).toBeVisible(),
      page.click('button[type="submit"]'),
    ])

    // 活动流：至少出现一个节点完成事件（演示图第一个是「规划问题」）
    await expect(page.getByText('规划问题').first()).toBeVisible({ timeout: 60_000 })

    // 终局：报告区渲染出来
    await expect(page.locator('[data-testid="report-heading"]')).toBeVisible({ timeout: 90_000 })
    const report = page.locator('article.report-prose')
    await expect(report).toContainText('演示研究报告')
    await expect(page.getByText('研究完成')).toBeVisible()
  })

  test('刷新页面后恢复当前运行并继续到报告', async ({ page }) => {
    await page.fill('#topic', '刷新恢复')
    await page.click('button[type="submit"]')
    await expect(page.getByText('研究进行中')).toBeVisible()
    // 先让回放里有真实内容（至少一个节点完成）
    await expect(page.getByText('规划问题').first()).toBeVisible({ timeout: 60_000 })

    await page.reload()

    // 恢复态：状态徽标与 run_id 都回来（run_id 从会话存储恢复）
    await expect(page.locator('[data-testid="status-badge"]')).toBeVisible()
    await expect(page.locator('text=/RUN [a-f0-9]{12}/')).toBeVisible({ timeout: 30_000 })
    // 回放重建活动流并继续消费后续事件，最终走到报告
    await expect(page.locator('[data-testid="report-heading"]')).toBeVisible({ timeout: 90_000 })
    await expect(page.getByText('研究完成')).toBeVisible()
  })

  test('完成后刷新仍能回看最终报告', async ({ page }) => {
    await page.fill('#topic', '完成后续看')
    await page.click('button[type="submit"]')
    await expect(page.locator('[data-testid="report-heading"]')).toBeVisible({ timeout: 90_000 })
    await expect(page.getByText('研究完成')).toBeVisible()

    await page.reload()

    // 终局后保留 run_id（#12）⇒ 回放重建最终态，刷新不丢报告
    await expect(page.locator('[data-testid="report-heading"]')).toBeVisible({ timeout: 60_000 })
    await expect(page.getByText('研究完成')).toBeVisible()
    await expect(page.locator('article.report-prose')).toContainText('演示研究报告')
  })

  test('降级事件在运行中实时可见', async ({ page }) => {
    await page.fill('#topic', '降级可见性')
    await page.click('button[type="submit"]')

    // 演示图在第 5 个节点注入一次 web_search 降级；这里断言它**在报告出现之前**就被推到界面。
    // 同一条降级会同时出现在「活动流」和「降级与恢复」两块 ⇒ 取第一个即可。
    await expect(page.getByText('Bocha 返回 429，已跳过该源').first()).toBeVisible({ timeout: 60_000 })
    // 回退动作同样要可见：降级卡写的是 `fallback_action=empty_list`，不是「已由后端处理」
    await expect(page.getByText('empty_list').first()).toBeVisible()
  })

  test('报告导出走后端接口并带上 run_id', async ({ page }) => {
    await page.fill('#topic', '导出验证')
    await page.click('button[type="submit"]')
    await expect(page.locator('[data-testid="report-heading"]')).toBeVisible({ timeout: 90_000 })

    const [download] = await Promise.all([
      page.waitForEvent('download'),
      page.click('[data-testid="export-button"]'),
    ])
    expect(download.suggestedFilename()).toMatch(/^deepresearch-[a-f0-9]+\.md$/)

    // 直接问后端要一份，核对导出内容**带审计元数据**（前端那份纯正文没有）
    const runId = download.suggestedFilename().replace(/^deepresearch-|\.md$/g, '')
    const response = await page.request.get(`/api/research/${runId}/report?format=md`)
    expect(response.ok()).toBeTruthy()
    const body = await response.text()
    expect(body).toContain(runId)
    expect(body).toContain('## 引用清单')
    expect(body).toContain('run_status')
  })

  test('运行中可停止，且不会被记成失败', async ({ page }) => {
    await page.fill('#topic', '取消语义')
    await page.click('button[type="submit"]')
    await expect(page.getByText('研究进行中')).toBeVisible()

    await page.click('button:has-text("停止研究")')
    await expect(page.getByText('已取消')).toBeVisible({ timeout: 60_000 })
    // 取消不是故障 ⇒ 不得出现错误卡、不得显示「运行失败」
    await expect(page.locator('[data-testid="error-card"]')).toHaveCount(0)
    await expect(page.getByText('运行失败')).toHaveCount(0)
  })
})
