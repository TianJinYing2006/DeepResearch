import { expect, test, type Locator } from '@playwright/test'

/** ICP 备案展示（合规强制）：落地页 / 工作台 / 只读分享页页脚，且必须是指向工信部的超链接。 */

const ICP_TEXT = '皖ICP备2026036120号'
const ICP_URL = 'https://beian.miit.gov.cn/'

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

async function expectBeianLink(scope: Locator) {
  const link = scope.getByTestId('beian-link')
  await expect(link).toBeVisible()
  await expect(link).toHaveText(ICP_TEXT)
  await expect(link).toHaveAttribute('href', ICP_URL)
  await expect(link).toHaveAttribute('target', '_blank')
  await expect(link).toHaveAttribute('rel', /noopener/)
}

test.describe('ICP 备案展示（桌面端）', () => {
  test('落地页页脚展示备案号超链接', async ({ page }) => {
    await page.route('**/api/options', (route) => route.fulfill({ json: AUTH_OPTIONS }))
    await page.route('**/api/auth/session', (route) =>
      route.fulfill({ status: 401, json: { detail: { code: 'unauthenticated', message: '未登录' } } }),
    )

    await page.goto('/')
    const landing = page.getByTestId('landing-page')
    await expect(landing).toBeVisible()
    await expectBeianLink(landing)
  })

  test('工作台页脚展示备案号超链接', async ({ page }) => {
    await page.goto('/')
    await expect(page.locator('#topic')).toBeVisible()
    await expectBeianLink(page.locator('body'))
  })

  test('只读分享页页脚展示备案号超链接', async ({ page }) => {
    await page.route('**/api/share/token-abcdefghijklmnop', (route) =>
      route.fulfill({
        json: {
          topic: '分享的报告主题',
          markdown: '# 报告正文\n\n这是只读分享内容',
          permanent: false,
          expires_at: '2026-10-11T00:00:00+00:00',
        },
      }),
    )

    await page.goto('/s/token-abcdefghijklmnop')
    await expect(page.getByTestId('share-topic')).toHaveText('分享的报告主题')
    await expectBeianLink(page.locator('body'))
  })
})
