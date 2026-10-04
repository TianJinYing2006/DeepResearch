import { expect, test } from '@playwright/test'

/** 需求 25：帮助中心 / 站内反馈 / 注册同意（路由拦截，确定性场景）。 */

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

test.describe('需求 25：帮助与反馈（桌面端）', () => {
  test('帮助弹窗渲染 FAQ', async ({ page }) => {
    await page.route('**/api/help/faq', (route) =>
      route.fulfill({ json: { markdown: '# 帮助中心\n\n## 账号与邀请\n\n内测采用邀请制。' } }),
    )

    await page.goto('/')
    await page.getByTestId('help-toggle').click()

    const modal = page.getByTestId('help-modal')
    await expect(modal).toBeVisible()
    await expect(modal).toContainText('帮助中心')
    await expect(modal).toContainText('内测采用邀请制')
  })

  test('反馈弹窗：未达最小长度禁用，提交后显示成功态', async ({ page }) => {
    const bodies: unknown[] = []
    await page.route('**/api/feedback', (route) => {
      bodies.push(route.request().postDataJSON())
      return route.fulfill({ json: { ok: true, feedback_id: 'fb-123' } })
    })

    await page.goto('/')
    await page.getByTestId('feedback-toggle').click()
    const modal = page.getByTestId('feedback-modal')
    await expect(modal).toBeVisible()
    await expect(modal.getByTestId('feedback-submit')).toBeDisabled()

    await modal.getByTestId('feedback-message').fill('上传后队列没有显示进度，希望修复')
    await modal.getByTestId('feedback-submit').click()

    await expect(modal.getByTestId('feedback-done')).toBeVisible()
    expect(bodies).toEqual([{
      category: 'bug',
      message: '上传后队列没有显示进度，希望修复',
      contact: null,
      page: 'workbench',
    }])
  })

  test('注册必须勾选同意协议', async ({ page }) => {
    await page.route('**/api/options', (route) => route.fulfill({ json: AUTH_OPTIONS }))
    await page.route('**/api/auth/session', (route) =>
      route.fulfill({ status: 401, json: { detail: { code: 'unauthenticated', message: '未登录' } } }),
    )

    await page.goto('/')
    await page.getByTestId('landing-cta').click()
    const gate = page.getByTestId('auth-gate')
    await gate.getByRole('button', { name: '有邀请码？去注册' }).click()

    const submit = gate.getByRole('button', { name: '注册并登录' })
    await expect(submit).toBeDisabled()
    await gate.getByTestId('auth-agree-terms').check()
    await expect(submit).toBeEnabled()
  })
})
