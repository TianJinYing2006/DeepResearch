import { expect, test } from '@playwright/test'

for (const scenario of ['cancelled', 'timeout', 'empty-completed', 'normal', 'partial', 'under-review'] as const) {
  test(`report availability: ${scenario}`, async ({ page }) => {
    const available = ['normal', 'partial', 'under-review'].includes(scenario)
    const review = scenario === 'under-review'
    const cancelled = scenario === 'cancelled' || scenario === 'partial'
    const stopReason = cancelled ? 'cancelled' : scenario === 'timeout' ? 'timeout' : 'completed'
    await page.route('**/api/options', (route) => route.fulfill({ json: { auth_required: false, share_enabled: true } }))
    await page.route('**/api/research', (route) => route.fulfill({ json: { run_id: 'availability000' } }))
    await page.route('**/api/research/availability000/stream*', (route) => route.fulfill({
      contentType: 'text/event-stream',
      body: `id: 0\nevent: RUN_FINISHED\ndata: ${JSON.stringify({
        type: 'RUN_FINISHED', run_id: 'availability000', timestamp: Date.now(),
        cancelled, stop_reason: stopReason, has_report: available, output_under_review: review,
        result: { report: available && !review ? '# Available report' : '', citations: [],
          validator_stats: {}, depth: 1, visited_sources: [], reflection_log: [] },
      })}\n\n`,
    }))
    await page.goto('/')
    await page.fill('#topic', 'Report availability regression')
    await page.click('button[type="submit"]')
    await expect(page.getByTestId('report-heading')).toBeVisible()
    if (!available) {
      await expect(page.getByTestId('workflow-interrupted')).toBeVisible()
      for (const stage of ['report', 'verify', 'export']) {
        await expect(page.getByTestId(`workflow-stage-${stage}`)).toHaveAttribute('data-stage-state', 'pending')
        await expect(page.getByTestId(`workflow-stage-${stage}`)).not.toHaveAttribute('href')
      }
      await expect(page.locator('#topic')).toBeVisible()
      await expect(page.getByText('暂无完整报告', { exact: true })).toBeVisible()
    } else {
      await expect(page.getByTestId('workflow-interrupted')).toHaveCount(0)
      await expect(page.getByTestId('workflow-stage-report')).toHaveAttribute('data-stage-state', 'done')
      if (review) await expect(page.getByText('报告待人工复核', { exact: true })).toBeVisible()
      else await expect(page.locator('#stage-report')).toContainText('Available report')
    }
    for (const action of [page.getByTestId('export-button'), page.getByTestId('share-button'), page.getByRole('button', { name: '复制正文' })]) {
      if (!available || review) await expect(action).toBeDisabled()
      else await expect(action).toBeEnabled()
    }
    await expect(page.getByTestId('workflow-stage-export')).toHaveAttribute('data-stage-state',
      !available ? 'pending' : review ? 'current' : 'done')
  })
}
