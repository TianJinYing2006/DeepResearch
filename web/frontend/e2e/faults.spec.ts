import { expect, test } from '@playwright/test'

import { settleRun } from './helpers'

/** 异常态回归（验收点④）：断线恢复 / 取消中 / 配额耗尽。
 *
 * 为什么单开一个 spec：
 * 这三条在**任何视口下都没有覆盖** —— 既不在 `research.spec`（只覆盖停止成功路径，
 * 不覆盖「停止请求在途」这个中间态），也不在 `error-handling.spec`（只有并发闸 429，
 * 没有配额耗尽的 `quota_exceeded`），更没有断线重连。
 *
 * 与 `responsive.spec` 不同，这三条与视口无关（判据是状态徽标与错误码，不是布局），
 * 因此**两个视口都跑**：窄屏下同样的状态机也必须成立。
 *
 * 全部用路由拦截或 DR_DEMO 假数据构造，零 LLM、零真实任务库占用。
 */
test.describe('异常态回归', () => {
  test.afterEach(async ({ page }) => {
    // 后端并发闸为 1：任何真的发起了研究的用例都必须等它终局，否则失败会传染（见 helpers.ts）
    await settleRun(page)
  })

  test('实时流断开时显示「正在重连」，恢复后仍能跑到报告', async ({ page }) => {
    // 只掐断**第一次**流请求：EventSource 会自动重连，第二次放行走真实后端。
    // 这样既验证了错误态呈现，也验证了「重连不会重新启动研究」（不是新开一个 run）。
    let abortedOnce = false
    await page.route('**/api/research/*/stream', async (route) => {
      if (abortedOnce) return route.fallback()
      abortedOnce = true
      await route.abort()
    })

    await page.goto('/')
    await page.fill('#topic', '断线恢复')
    await page.click('button[type="submit"]')

    // 连接状态徽标在页头（与运行状态徽标是两个独立通道）
    await expect(page.locator('header')).toContainText('正在重连', { timeout: 30_000 })
    // 恢复的判据不是「徽标变回绿的」，而是**报告真的产出** —— 否则只证明 UI 改了字
    await expect(page.getByTestId('report-heading')).toBeVisible({ timeout: 90_000 })
    // 终局后 EventSource 正常关闭 ⇒ 页头最终是「连接已关闭」而不是「实时连接」。
    // 因此这里只断言「已经脱离重连态」，不断言具体的终态文案。
    await expect(page.locator('header')).not.toContainText('正在重连')
  })

  test('取消请求在途时状态呈现「正在安全停止」', async ({ page }) => {
    // 把取消请求拖住 3s，制造「已请求取消、后端尚未确认」的窗口
    await page.route('**/api/research/*/cancel', async (route) => {
      await new Promise((resolve) => setTimeout(resolve, 3_000))
      await route.fallback()
    })

    await page.goto('/')
    await page.fill('#topic', '取消中状态')
    await page.click('button[type="submit"]')
    await expect(page.getByTestId('status-badge')).toContainText('研究进行中', { timeout: 30_000 })

    // R4b：停止为两步确认
    await page.locator('button:has-text("停止研究")').first().click()
    await page.locator('[data-testid="stop-confirm"]').click()

    // 关键判据：这一档**不是**「已取消」——它是「已请求、等后端在安全边界停」。
    // 把中间态显示成终态，用户会以为已经停了而去关页面（D-19 前台模型下等于丢任务）。
    await expect(page.getByTestId('status-badge')).toContainText('正在安全停止')
  })

  test('配额耗尽时展示结构化错误码而非整句文案', async ({ page }) => {
    await page.route('**/api/research', async (route) => {
      if (route.request().method() !== 'POST') return route.fallback()
      await route.fulfill({
        status: 429,
        contentType: 'application/json',
        body: JSON.stringify({
          detail: {
            code: 'quota_exceeded',
            message: '本月预算已用完，新任务暂不可提交',
            component: 'web',
            node: null,
            detail: 'month_cost_cny=10.00; monthly_budget_cny=10.00',
            retryable: false,
            hint: '月度预算熔断：下月自动恢复，或联系管理员调整预算。',
          },
        }),
      })
    })

    await page.goto('/')
    await page.fill('#topic', '配额耗尽')
    await page.click('button[type="submit"]')

    const card = page.getByTestId('error-card')
    await expect(card).toBeVisible()
    await expect(page.getByTestId('error-code')).toHaveText('quota_exceeded')
    await expect(page.getByTestId('error-hint')).toContainText('月度预算')
    // retryable=false ⇒ 不提供「用同样参数重试」这个注定失败的入口
    await expect(page.getByTestId('retry-button')).toHaveCount(0)
  })
})
