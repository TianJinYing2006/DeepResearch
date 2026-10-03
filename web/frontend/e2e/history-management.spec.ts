import { expect, test } from '@playwright/test'

// 需求 22：历史任务管理（搜索 / 重命名 / 归档 / 失败重试）—— mock /api/runs，UI 行为可重复。
// 与 account.spec.ts 同口径：走 DR_DEMO 演示服务器，所有 runs 接口由本文件接管。

type RunRow = {
  run_id: string
  topic: string
  status: string
  created_at: string
  has_report: boolean
  pinned_at?: string | null
  archived_at?: string | null
}

const row = (overrides: Partial<RunRow> & Pick<RunRow, 'run_id' | 'topic' | 'status'>): RunRow => ({
  created_at: '2026-10-03T10:00:00Z',
  has_report: false,
  ...overrides,
})

test.describe('需求 22 历史任务管理（桌面端）', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/')
  })

  test('搜索：关键词透传后端并过滤列表', async ({ page }) => {
    let lastQuery = ''
    const rows = [
      row({ run_id: 'run-quantum', topic: '量子计算综述', status: 'SUCCEEDED', has_report: true }),
      row({ run_id: 'run-protein', topic: '蛋白质折叠', status: 'SUCCEEDED' }),
    ]
    await page.route('**/api/runs**', (route) => {
      lastQuery = new URL(route.request().url()).searchParams.get('q') ?? ''
      const filtered = lastQuery ? rows.filter((item) => item.topic.includes(lastQuery)) : rows
      return route.fulfill({ json: { runs: filtered, limit: 10, offset: 0 } })
    })

    await page.getByTestId('history-toggle').click()
    await expect(page.getByTestId('history-panel')).toBeVisible()
    await expect(page.getByTestId('history-panel')).toContainText('量子计算综述')

    await page.getByTestId('history-search').fill('量子')
    await expect(page.getByTestId('history-panel')).not.toContainText('蛋白质折叠')
    await expect(page.getByTestId('history-panel')).toContainText('量子计算综述')
    expect(lastQuery).toBe('量子')
  })

  test('重命名：PATCH 提交新主题并刷新列表', async ({ page }) => {
    const rows = [row({ run_id: 'run-1', topic: '旧主题', status: 'SUCCEEDED' })]
    let patched: { topic?: string } = {}
    await page.route('**/api/runs**', (route) => route.fulfill({ json: { runs: rows, limit: 10, offset: 0 } }))
    await page.route('**/api/runs/run-1', (route) => {
      if (route.request().method() !== 'PATCH') return route.fallback()
      patched = route.request().postDataJSON() as { topic?: string }
      rows[0].topic = patched.topic ?? rows[0].topic
      return route.fulfill({ json: { ok: true, run_id: 'run-1', topic: rows[0].topic } })
    })

    await page.getByTestId('history-toggle').click()
    await page.getByTestId('history-rename').click()
    await expect(page.getByTestId('history-rename-input')).toHaveValue('旧主题')
    await page.getByTestId('history-rename-input').fill('新主题')
    await page.getByTestId('history-rename-save').click()

    await expect(page.getByTestId('history-panel')).toContainText('新主题')
    expect(patched.topic).toBe('新主题')
  })

  test('归档移除 + 失败任务一键重试', async ({ page }) => {
    let archived = false
    let retried = false
    const rows = [row({ run_id: 'run-fail', topic: '失败任务', status: 'FAILED' })]
    await page.route('**/api/runs**', (route) => route.fulfill({
      json: { runs: archived ? [] : rows, limit: 10, offset: 0 },
    }))
    await page.route('**/api/runs/run-fail/archive', (route) => {
      archived = true
      return route.fulfill({ json: { ok: true, run_id: 'run-fail', archived: true } })
    })
    await page.route('**/api/runs/run-fail/retry', (route) => {
      retried = true
      return route.fulfill({ json: { run_id: 'run-new' } })
    })

    await page.getByTestId('history-toggle').click()
    await page.getByTestId('history-run-retry').click()
    await expect.poll(() => retried).toBe(true)

    await page.getByTestId('history-archive').click()
    await expect(page.getByTestId('history-empty')).toBeVisible()
    expect(archived).toBe(true)
  })
})
