import { expect, test, type Page } from '@playwright/test'

function deferred() {
  let resolve = () => {}
  const promise = new Promise<void>((done) => { resolve = done })
  return { promise, resolve }
}

const document = (source: string) => ({ doc_id: source, source, chunks: 1, status: 'ready' })

async function mockAccount(page: Page) {
  await page.route('**/api/options', (route) => route.fulfill({ json: { auth_required: false } }))
  await page.route('**/api/auth/session', (route) =>
    route.fulfill({ json: { user: { user_id: 'account-a', email: 'a@example.com' } } }),
  )
  await page.route('**/api/quota', (route) => route.fulfill({ json: {} }))
  await page.route('**/api/rag/usage', (route) => route.fulfill({ json: { used_bytes: 0, quota_bytes: null } }))
}

test('logout invalidates a delayed account document refresh', async ({ page }) => {
  const started = deferred()
  const release = deferred()
  const delivered = deferred()
  let hold = false
  let loggedOut = false
  await mockAccount(page)
  await page.route('**/api/auth/logout', async (route) => {
    loggedOut = true
    await route.fulfill({ json: {} })
  })
  await page.route('**/api/rag/docs', async (route) => {
    const oldAccount = !loggedOut
    if (hold && oldAccount) { started.resolve(); await release.promise }
    await route.fulfill({ json: { docs: oldAccount ? [document('account-a.md')] : [] } })
    if (hold && oldAccount) delivered.resolve()
  })
  await page.goto('/')
  await expect(page.getByTestId('account-email')).toHaveText('a@example.com')
  await page.getByTestId('kb-toggle').click()
  await expect(page.getByTestId('kb-doc-item')).toContainText('account-a.md')
  hold = true
  await page.getByTestId('kb-refresh').click()
  await started.promise
  await page.getByTestId('logout-button').click()
  await expect(page.getByTestId('account-email')).toHaveCount(0)
  await page.getByTestId('kb-toggle').click()
  await expect(page.getByText('还没有上传文档', { exact: true })).toBeVisible()
  await page.waitForTimeout(200)
  release.resolve()
  await delivered.promise
  await page.waitForTimeout(200)
  await expect(page.getByTestId('kb-doc-item')).toHaveCount(0, { timeout: 1000 })
})

test('a newer document refresh wins over an older delayed response', async ({ page }) => {
  const started = deferred()
  const release = deferred()
  const delivered = deferred()
  let holdNext = false
  let current = 'initial.md'
  await mockAccount(page)
  await page.route('**/api/rag/docs', async (route) => {
    const held = holdNext
    holdNext = false
    const source = current
    if (held) { started.resolve(); await release.promise }
    await route.fulfill({ json: { docs: [document(source)] } })
    if (held) delivered.resolve()
  })
  await page.goto('/')
  await expect(page.getByTestId('account-email')).toBeVisible()
  await page.getByTestId('kb-toggle').click()
  await expect(page.getByTestId('kb-doc-item')).toContainText('initial.md')
  holdNext = true
  current = 'older.md'
  await page.getByTestId('kb-refresh').click()
  await started.promise
  current = 'newer.md'
  await page.getByTestId('kb-refresh').click()
  await expect(page.getByTestId('kb-doc-item')).toContainText('newer.md')
  release.resolve()
  await delivered.promise
  await page.waitForTimeout(200)
  await expect(page.getByTestId('kb-doc-item')).toContainText('newer.md', { timeout: 1000 })
})
