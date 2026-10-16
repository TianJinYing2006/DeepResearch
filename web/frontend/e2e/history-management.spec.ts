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

  test('历史报告导出：md / PDF（异步作业）/ DOCX 三格式与主视图对齐', async ({ page }) => {
    const runId = 'run-hist-export'
    const disposition = (name: string) =>
      `attachment; filename="hist.${name.split('.').pop()}"; filename*=UTF-8''${encodeURIComponent(name)}`
    const rows = [row({ run_id: runId, topic: '历史导出验证', status: 'SUCCEEDED', has_report: true })]
    await page.route('**/api/runs**', (route) => route.fulfill({ json: { runs: rows, limit: 10, offset: 0 } }))
    await page.route(`**/api/research/${runId}/report?format=md`, (route) => route.fulfill({
      contentType: 'text/markdown; charset=utf-8',
      headers: { 'content-disposition': disposition('历史导出验证.md') },
      body: '# 历史报告\n\n正文段落。',
    }))
    await page.route(`**/api/research/${runId}/report?format=docx`, (route) => route.fulfill({
      contentType: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
      headers: { 'content-disposition': disposition('历史导出验证.docx') },
      body: 'PK-docx-bytes',
    }))
    // PDF 异步作业三端点（创建 202 → 状态 SUCCEEDED → 授权下载）
    await page.route(`**/api/research/${runId}/export-jobs`, (route) => route.fulfill({
      status: 202, json: { export_id: 'job-hist-1', status: 'QUEUED', reused: false },
    }))
    await page.route(`**/api/research/${runId}/export-jobs/job-hist-1`, (route) => route.fulfill({
      json: { export_id: 'job-hist-1', status: 'SUCCEEDED', error_code: null,
              result_bytes: 12, result_expires_at: null },
    }))
    await page.route(`**/api/research/${runId}/export-jobs/job-hist-1/download`, (route) => route.fulfill({
      contentType: 'application/pdf',
      headers: { 'content-disposition': disposition('历史导出验证.pdf') },
      body: '%PDF-1.4 mock',
    }))

    await page.getByTestId('history-toggle').click()
    await page.getByRole('button', { name: '查看报告' }).click()
    await expect(page.getByTestId('history-preview')).toBeVisible()
    await expect(page.getByTestId('history-preview')).toContainText('历史报告')

    const [md] = await Promise.all([
      page.waitForEvent('download'),
      page.getByTestId('history-export').click(),
    ])
    expect(md.suggestedFilename()).toBe('历史导出验证.md')

    const [pdf] = await Promise.all([
      page.waitForEvent('download', { timeout: 30_000 }),
      page.getByTestId('history-export-pdf').click(),
    ])
    expect(pdf.suggestedFilename()).toBe('历史导出验证.pdf')

    const [docx] = await Promise.all([
      page.waitForEvent('download'),
      page.getByTestId('history-export-docx').click(),
    ])
    expect(docx.suggestedFilename()).toBe('历史导出验证.docx')

    // 三格式导出完成后按钮回到可用态（无残留忙碌态）
    await expect(page.getByTestId('history-export')).toBeEnabled()
    await expect(page.getByTestId('history-export-pdf')).toBeEnabled()
  })
})
