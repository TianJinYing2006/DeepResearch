import { expect, test } from '@playwright/test'

/** 需求 24：自助找回 / 重置密码（路由拦截构造确定性场景；不依赖任务库与真实邮件通道）。 */

const AUTH_OPTIONS = {
  search_providers: [],
  default_provider: 'bocha',
  enable_arxiv_default: false,
  max_total_hops_default: 2,
  max_subquestions_default: 2,
  run_timeout_seconds: 3,
  auth_required: true,
  invite_only: true,
}

async function mockUnauthenticated(page: import('@playwright/test').Page) {
  await page.route('**/api/options', (route) => route.fulfill({ json: AUTH_OPTIONS }))
  await page.route('**/api/auth/session', (route) =>
    route.fulfill({ status: 401, json: { detail: { code: 'unauthenticated', message: '未登录' } } }),
  )
}

test.describe('需求 24：自助找回（桌面端）', () => {
  test('登录门提供忘记密码入口，提交后显示中性提示与重发按钮', async ({ page }) => {
    await mockUnauthenticated(page)
    const forgotBodies: unknown[] = []
    await page.route('**/api/auth/forgot', (route) => {
      forgotBodies.push(route.request().postDataJSON())
      return route.fulfill({ json: { ok: true } })
    })

    await page.goto('/')
    await page.getByTestId('landing-cta').click()
    const gate = page.getByTestId('auth-gate')
    await gate.getByTestId('auth-forgot-link').click()
    await expect(gate.getByRole('heading', { name: '找回密码' })).toBeVisible()

    await gate.locator('#auth-email').fill('demo@example.com')
    await gate.getByTestId('auth-forgot-submit').click()

    await expect(gate.getByTestId('auth-forgot-sent')).toBeVisible()
    await expect(gate.getByTestId('auth-forgot-resend')).toContainText('重新发送')
    expect(forgotBodies).toEqual([{ email: 'demo@example.com' }])
  })

  test('重置链接失效时给出重新申请出口', async ({ page }) => {
    await mockUnauthenticated(page)
    await page.route('**/api/auth/reset', (route) =>
      route.fulfill({
        status: 422,
        json: { detail: { code: 'invalid_request', message: '重置链接无效或已过期，请重新申请' } },
      }),
    )

    await page.goto('/#reset=expired-token-123456')
    const gate = page.getByTestId('auth-gate')
    await expect(gate.getByRole('heading', { name: '设置新密码' })).toBeVisible()

    await gate.getByTestId('auth-reset-password').fill('password-123456')
    await gate.getByTestId('auth-reset-confirm').fill('password-123456')
    await gate.getByTestId('auth-reset-submit').click()

    await expect(gate.getByTestId('auth-reset-invalid')).toBeVisible()
    await gate.getByRole('button', { name: '重新申请' }).click()
    await expect(gate.getByRole('heading', { name: '找回密码' })).toBeVisible()
  })

  test('重置成功后清除链接并回到登录', async ({ page }) => {
    await mockUnauthenticated(page)
    let resetBody: unknown = null
    await page.route('**/api/auth/reset', (route) => {
      resetBody = route.request().postDataJSON()
      return route.fulfill({ json: { ok: true } })
    })

    await page.goto('/#reset=good-token-1234567890')
    const gate = page.getByTestId('auth-gate')
    await gate.getByTestId('auth-reset-password').fill('new-password-1234')
    await gate.getByTestId('auth-reset-confirm').fill('new-password-1234')
    await expect(gate.getByText('两次输入一致')).toBeVisible()
    await gate.getByTestId('auth-reset-submit').click()

    await expect(gate.getByTestId('auth-reset-done')).toBeVisible()
    expect(resetBody).toEqual({ token: 'good-token-1234567890', new_password: 'new-password-1234' })
    expect(page.url()).not.toContain('#reset=')

    await gate.getByRole('button', { name: '返回登录' }).click()
    await expect(gate.getByRole('heading', { name: '登录 DeepResearch' })).toBeVisible()
  })
})
