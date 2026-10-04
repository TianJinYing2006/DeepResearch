import { expect, test } from '@playwright/test'

/** 需求 26：报告只读分享页（路由拦截，确定性场景）。 */

test.describe('需求 26：只读分享页（桌面端）', () => {
  test('有效链接渲染报告且无操作按钮', async ({ page }) => {
    await page.route('**/api/share/token-abcdefghijklmnop', (route) =>
      route.fulfill({
        json: {
          topic: '分享的报告主题',
          markdown: '# 报告正文\n\n这是只读分享内容。',
          permanent: false,
          expires_at: '2026-10-11T00:00:00+00:00',
        },
      }),
    )

    await page.goto('/s/token-abcdefghijklmnop')

    await expect(page.getByTestId('share-topic')).toHaveText('分享的报告主题')
    await expect(page.getByText('这是只读分享内容')).toBeVisible()
    await expect(page.getByTestId('export-button')).toHaveCount(0)
    await expect(page.getByTestId('share-button')).toHaveCount(0)
  })

  test('失效链接显示统一失效页', async ({ page }) => {
    await page.route('**/api/share/bad-token-1234567890', (route) =>
      route.fulfill({
        status: 404,
        json: { detail: { code: 'share_not_found', message: '分享链接无效或已过期。' } },
      }),
    )

    await page.goto('/s/bad-token-1234567890')

    await expect(page.getByTestId('share-invalid')).toBeVisible()
    await expect(page.getByTestId('share-invalid')).toContainText('链接无效或已过期')
  })
})
