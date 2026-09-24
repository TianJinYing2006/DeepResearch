import { expect, test } from '@playwright/test'

import { settleRun } from './helpers'

/** 结构化错误 + 重试（P1-5 / P1-7）。

 用**路由拦截**制造确定性错误：真实后端要触发 429 得真的并发跑两个研究（慢且不稳定），
 而这里要验证的是「前端拿到结构化错误后的呈现与重试动作」，与错误怎么产生无关。 */
test.describe('错误呈现与重试', () => {
  test.afterEach(async ({ page }) => {
    // 重试那条用例真的会发起一次研究 ⇒ 必须等它结束，否则并发闸挡住后续用例
    await settleRun(page)
  })

  test('后端返回 429 时展示错误码与建议，并可原参数重试', async ({ page }) => {
    let failNext = true
    await page.route('**/api/research', async (route) => {
      if (route.request().method() !== 'POST') return route.fallback()
      if (!failNext) return route.fallback()
      failNext = false
      await route.fulfill({
        status: 429,
        contentType: 'application/json',
        body: JSON.stringify({
          detail: {
            code: 'concurrency_limit',
            message: '已有 1 个研究在运行，上限 1',
            component: 'web',
            node: null,
            detail: 'active=1; limit=1',
            retryable: true,
            hint: '已有研究在运行：前台模型下一次只跑一个（D-19）。',
          },
        }),
      })
    })

    await page.goto('/')
    await page.fill('#topic', '并发闸门')
    await page.click('button[type="submit"]')

    const card = page.locator('[data-testid="error-card"]')
    await expect(card).toBeVisible()
    // 只认**键**：错误码与建议必须结构化地呈现，而不是把整句 message 糊上去
    await expect(page.locator('[data-testid="error-code"]')).toHaveText('concurrency_limit')
    await expect(page.locator('[data-testid="error-hint"]')).toContainText('前台模型')
    // 详情默认折叠（#12）：展开后再核对，避免长 detail 挤占错误卡
    await page.locator('[data-testid="error-detail"] summary').click()
    await expect(card).toContainText('active=1; limit=1')

    // 重试：第二次请求放行 ⇒ 应真的跑起来（不是断点续跑，D-19 没有可续跑的中间态）
    await page.click('[data-testid="retry-button"]')
    await expect(page.getByText('研究进行中')).toBeVisible({ timeout: 30_000 })
    await expect(card).toHaveCount(0)
  })

  test('未知 run_id 的状态查询返回结构化 404', async ({ page, request }) => {
    const response = await request.get('/api/research/does-not-exist')
    expect(response.status()).toBe(404)
    const detail = (await response.json()).detail
    expect(detail.code).toBe('run_id_not_found')
    expect(typeof detail.hint).toBe('string')
    expect(detail.hint.length).toBeGreaterThan(0)
  })
})
