import { expect, test } from '@playwright/test'

/** 审计 #328：登出/切换账号必须清空 App 级运行域与三个会话存储键。
 *
 * 全部走**路由拦截**（与 closure.spec 同思路）：不依赖真实任务库 / LLM / SSE，
 * 断言点是「登出后 sessionStorage 三个键被移除」+「换账号后草稿不闪回」。 */

const AUTH_OPTIONS = {
  search_providers: [],
  default_provider: 'bocha',
  enable_arxiv_default: false,
  max_total_hops_default: 2,
  max_subquestions_default: 2,
  run_timeout_seconds: 3,
  auth_required: true,
}

const SIGNED_OUT_KEYS = ['dr.lastRunId', 'dr.draft', 'dr.lastRequest']

test.describe('登出清理（需求 42 / 审计 #328）', () => {
  test('登出后清空三会话键，换账号不闪回上一账号草稿', async ({ page }) => {
    let loggedIn = true
    let sessionUser = { user_id: 'u-a', email: 'a@example.com' }

    await page.route('**/api/options', (route) => route.fulfill({ json: AUTH_OPTIONS }))
    await page.route('**/api/auth/session', (route) => {
      if (!loggedIn) {
        return route.fulfill({
          status: 401,
          json: { detail: { code: 'unauthenticated', message: '未登录' } },
        })
      }
      return route.fulfill({ json: { user: sessionUser } })
    })
    await page.route('**/api/quota', (route) => route.fulfill({ json: {} }))
    await page.route('**/api/rag/usage', (route) =>
      route.fulfill({ json: { used_bytes: 0, quota_bytes: null } }),
    )
    await page.route('**/api/rag/docs', (route) => route.fulfill({ json: { docs: [] } }))
    await page.route('**/api/auth/logout', (route) => {
      loggedIn = false
      return route.fulfill({ json: { ok: true } })
    })
    await page.route('**/api/auth/login', (route) => {
      sessionUser = { user_id: 'u-b', email: 'b@example.com' }
      loggedIn = true
      return route.fulfill({ json: { user: sessionUser } })
    })

    await page.goto('/')
    await expect(page.getByTestId('account-email')).toHaveText('a@example.com')

    // 账号 A 写草稿：等 500ms 防抖把 dr.draft 落盘
    await page.locator('#topic').fill('账号A的私有主题')
    await page.waitForTimeout(700)
    expect(await page.evaluate(() => sessionStorage.getItem('dr.draft') ?? '')).toContain(
      '账号A的私有主题',
    )

    // 补齐另外两个会话级键，确保断言覆盖「三个键都要清」
    await page.evaluate(() => {
      sessionStorage.setItem('dr.lastRunId', 'run-account-a')
      sessionStorage.setItem(
        'dr.lastRequest',
        JSON.stringify({ topic: '账号A的私有主题', instructions: '', profile: 'quick' }),
      )
    })

    await page.getByTestId('logout-button').click()

    // 鉴权开启时登出 → 落地页；账号条不再展示账号 A
    await expect(page.getByTestId('landing-page')).toBeVisible()
    await expect(page.getByTestId('account-email')).toHaveCount(0)

    // #328 核心断言：三个会话级键全部移除（含刚写入的 lastRunId/lastRequest）
    expect(
      await page.evaluate((keys) => keys.map((key) => sessionStorage.getItem(key)), SIGNED_OUT_KEYS),
    ).toEqual([null, null, null])

    // 换账号 B 登录：A 的草稿不得被填回表单
    await page.getByTestId('landing-cta').click()
    const gate = page.getByTestId('auth-gate')
    await expect(gate).toBeVisible()
    await gate.locator('#auth-email').fill('b@example.com')
    await gate.locator('#auth-password').fill('password1234')
    await gate.getByRole('button', { name: '登录', exact: true }).click()

    await expect(page.getByTestId('account-email')).toHaveText('b@example.com')
    await expect(page.locator('#topic')).toHaveValue('')
  })
})
