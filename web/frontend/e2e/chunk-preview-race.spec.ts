import { expect, test } from '@playwright/test'

const docs = [
  { doc_id: 'race:a', source: 'A.md', chunks: 1, status: 'ready' },
  { doc_id: 'race:b', source: 'B.md', chunks: 1, status: 'ready' },
]
const chunks = (name: string) => ({ total: 1, chunks: [
  { chunk_id: name, chunk_index: 0, locator: {}, text: `${name}_CONTENT` },
] })

for (const responseKind of ['success', 'error', 'malformed'] as const) {
  test(`a delayed ${responseKind} cannot replace the next preview`, async ({ page }) => {
    let release = () => {}
    let arrived = () => {}
    const held = new Promise<void>((resolve) => { release = resolve })
    const started = new Promise<void>((resolve) => { arrived = resolve })
    await page.route('**/api/rag/docs', (route) => route.fulfill({ json: { docs } }))
    await page.route('**/api/rag/docs/race%3Aa/chunks*', async (route) => {
      arrived()
      await held
      if (responseKind === 'error') {
        await route.fulfill({ status: 500, json: { detail: { message: 'A_ERROR' } } })
      } else if (responseKind === 'malformed') {
        await route.fulfill({ contentType: 'application/json', body: '{broken' })
      } else {
        await route.fulfill({ json: chunks('A') })
      }
    })
    await page.route('**/api/rag/docs/race%3Ab/chunks*', (route) => route.fulfill({ json: chunks('B') }))
    await page.goto('/')
    await page.getByTestId('kb-toggle').click()
    await page.getByTestId('kb-doc-preview').first().click()
    await started
    const preview = page.getByTestId('kb-preview')
    await expect(preview).toContainText('A.md')
    await preview.getByRole('button', { name: '关闭', exact: true }).click()
    await page.getByTestId('kb-doc-preview').nth(1).click()
    await expect(preview).toContainText('B_CONTENT')
    const late = page.waitForResponse((response) => response.url().includes('race%3Aa/chunks'))
    release()
    await late
    await page.waitForTimeout(300)
    await expect(preview).toContainText('B.md')
    await expect(preview).toContainText('B_CONTENT')
    await expect(preview.locator('[role="alert"]')).toHaveCount(0)
    await expect(preview).not.toContainText('A_CONTENT')
  })
}
