import { expect, test } from '@playwright/test'

for (const failed of ['quota', 'docs', 'usage', 'docs-http'] as const) {
  test(`side data isolates ${failed} failures`, async ({ page }) => {
    const errors: string[] = []
    page.on('pageerror', (error) => errors.push(error.message))
    await page.route('**/api/options', (route) => route.fulfill({ json: { auth_required: false } }))
    await page.route('**/api/auth/session', (route) => route.fulfill({
      json: { user: { user_id: 'json-test', email: 'json@example.com' } },
    }))
    const bodies = {
      quota: { daily_runs_used: 2, daily_runs_limit: 7, user_active_runs: 0,
        user_concurrent_limit: 1, monthly_cost_cny: 0, monthly_budget_cny: 10 },
      docs: { docs: [{ doc_id: 'json-doc', source: 'valid.md', chunks: 1, status: 'ready' }] },
      usage: { used_bytes: 1024, quota_bytes: 1048576 },
    }
    for (const resource of ['quota', 'docs', 'usage'] as const) {
      const endpoint = resource === 'quota' ? 'quota' : `rag/${resource}`
      await page.route(`**/api/${endpoint}`, async (route) => {
        if (resource === failed) {
          await route.fulfill({ contentType: 'application/json', body: '{malformed' })
        } else if (resource === 'docs' && failed === 'docs-http') {
          await route.fulfill({ status: 503, json: { detail: { message: 'DOC_SERVICE_UNAVAILABLE' } } })
        } else {
          await route.fulfill({ json: bodies[resource] })
        }
      })
    }
    await page.goto('/')
    await expect(page.getByTestId('account-email')).toBeVisible()
    await page.getByTestId('kb-toggle').click()
    if (failed.startsWith('docs')) {
      await expect(page.getByTestId('kb-error')).toContainText(failed === 'docs-http'
        ? 'DOC_SERVICE_UNAVAILABLE' : '网络错误，请重试')
      await expect(page.getByTestId('kb-doc-item')).toHaveCount(0)
    } else {
      await expect(page.getByTestId('kb-doc-item')).toContainText('valid.md')
      await expect(page.getByTestId('kb-error')).toHaveCount(0)
    }
    if (failed === 'quota') await expect(page.getByTestId('quota-chip')).toHaveCount(0)
    else await expect(page.getByTestId('quota-chip')).toContainText('今日 2/7')
    if (failed === 'usage') await expect(page.getByTestId('kb-usage')).toHaveCount(0)
    else await expect(page.getByTestId('kb-usage')).toContainText('已用')
    await page.waitForTimeout(200)
    expect(errors).toEqual([])
  })
}
