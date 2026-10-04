import { expect, test } from '@playwright/test'

// P6-A：账号/知识库入口在「未配置任务库 / 无 Qdrant」时的降级表现，以及上传类型校验。
// 鉴权开启后的登录门 E2E 需要真实任务库，暂由后端用例（tests/test_rag_api.py、test_auth_api.py）覆盖。
test.describe('账号与知识库入口（桌面端）', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/')
  })

  test('未配置任务库时历史入口给出结构化提示', async ({ page }) => {
    await page.getByTestId('history-toggle').click()
    await expect(page.getByTestId('history-error')).toContainText('未配置任务库')
  })

  test('账号条与上传入口可用，且不影响主流程', async ({ page }) => {
    await expect(page.getByTestId('account-panel')).toBeVisible()
    await expect(page.getByTestId('rag-upload-input')).toBeAttached()
    // 需求 23 格式白名单对齐（PPT / Excel / HTML 可被选择）
    await expect(page.getByTestId('rag-upload-input')).toHaveAttribute(
      'accept', expect.stringContaining('.pptx'),
    )
    await expect(page.getByTestId('rag-upload-input')).toHaveAttribute(
      'accept', expect.stringContaining('.xlsx'),
    )
    await expect(page.getByTestId('rag-upload-input')).toHaveAttribute(
      'accept', expect.stringContaining('.html'),
    )
    await expect(page.getByTestId('kb-toggle')).toBeVisible()
    await expect(page.getByRole('button', { name: /开始研究/ })).toBeVisible()
  })

  test('上传不支持的文件类型被明确拒绝', async ({ page }) => {
    await page.getByTestId('rag-upload-input').setInputFiles({
      name: 'evil.exe',
      mimeType: 'application/octet-stream',
      buffer: Buffer.from('not-a-document'),
    })
    await expect(page.getByTestId('upload-state')).toContainText('不支持的文件类型')
  })

  // ---- P6-A 增量：知识库列表 + 上传进度（mock 后端，UI 行为可重复） ----

  test('知识库面板列出已上传文件', async ({ page }) => {
    await page.route('**/api/rag/docs', (route) =>
      route.fulfill({
        json: {
          docs: [
            { doc_id: 'local:aaaa1111', source: '行业报告.pdf', chunks: 12 },
            { doc_id: 'local:bbbb2222', source: 'notes.md', chunks: 3 },
          ],
        },
      }),
    )
    await page.getByTestId('kb-toggle').click()
    await expect(page.getByTestId('kb-panel')).toBeVisible()
    const rows = page.getByTestId('kb-doc-item')
    await expect(rows).toHaveCount(2)
    await expect(rows.filter({ hasText: '行业报告.pdf' })).toContainText('12 块')
    await expect(rows.filter({ hasText: 'notes.md' })).toContainText('3 块')
  })

  test('上传文件逐行展示进度条与状态', async ({ page }) => {
    await page.route('**/api/rag/ingest', (route) =>
      route.fulfill({ status: 200, json: { source: 'notes.md', chunks: 3, doc_id: 'local:abc' } }),
    )
    await page.route('**/api/rag/docs', (route) =>
      route.fulfill({ json: { docs: [{ doc_id: 'local:abc', source: 'notes.md', chunks: 3 }] } }),
    )
    await page.getByTestId('rag-upload-input').setInputFiles({
      name: 'notes.md',
      mimeType: 'text/markdown',
      buffer: Buffer.from('# hello'),
    })
    const row = page.getByTestId('upload-item').filter({ hasText: 'notes.md' })
    await expect(row).toBeVisible()
    await expect(row.getByTestId('upload-progress')).toBeAttached()
    await expect(row).toContainText('已入库（3 块）')
  })

  test('多文件选择逐行展示上传队列', async ({ page }) => {
    await page.route('**/api/rag/ingest', (route) =>
      route.fulfill({ status: 200, json: { source: 'a.md', chunks: 1, doc_id: 'local:a' } }),
    )
    await page.route('**/api/rag/docs', (route) => route.fulfill({ json: { docs: [] } }))
    await page.getByTestId('rag-upload-input').setInputFiles([
      { name: 'a.md', mimeType: 'text/markdown', buffer: Buffer.from('# a') },
      { name: 'b.md', mimeType: 'text/markdown', buffer: Buffer.from('# b') },
    ])
    await expect(page.getByTestId('upload-item').filter({ hasText: 'a.md' })).toBeVisible()
    await expect(page.getByTestId('upload-item').filter({ hasText: 'b.md' })).toBeVisible()
  })

  test('异步摄取协议：处理中显示耗时反馈，终态转为已入库', async ({ page }) => {
    await page.route('**/api/rag/ingest', (route) =>
      route.fulfill({
        status: 202,
        json: { ingestion_id: 'ing-async-1', doc_id: 'local:x', source: 'async.md', status: 'pending' },
      }),
    )
    let polls = 0
    await page.route('**/api/rag/ingestions/ing-async-1', (route) => {
      polls += 1
      return route.fulfill({
        json: {
          ingestion_id: 'ing-async-1',
          status: polls < 2 ? 'processing' : 'ready',
          chunks: 4, source: 'async.md', error: null,
        },
      })
    })
    await page.route('**/api/rag/docs', (route) => route.fulfill({ json: { docs: [] } }))

    await page.getByTestId('rag-upload-input').setInputFiles({
      name: 'async.md',
      mimeType: 'text/markdown',
      buffer: Buffer.from('# async'),
    })
    const row = page.getByTestId('upload-item').filter({ hasText: 'async.md' })
    await expect(row).toContainText('解析与嵌入中…', { timeout: 5000 })
    await expect(row).toContainText('已入库（4 块）', { timeout: 10000 })
    await expect(row.getByTestId('upload-progress')).toHaveAttribute('aria-valuetext', '已入库（4 块）')
  })

  // ---- P6-B：邀请链接 / 历史筛选与分页 ----

  test('弹窗支持 Escape 关闭且焦点不逃逸', async ({ page }) => {
    await page.goto('/?invite=invite-abc')
    await page.getByTestId('invite-register').getByRole('button', { name: '隐私政策' }).click()
    await expect(page.getByTestId('legal-modal')).toBeVisible()

    // R1：焦点陷阱 —— 连续 Tab 后焦点仍在弹窗内
    for (let index = 0; index < 12; index += 1) {
      await page.keyboard.press('Tab')
    }
    const trapped = await page.evaluate(
      () => document.activeElement?.closest('[data-testid="legal-modal"]') !== null,
    )
    expect(trapped).toBe(true)

    // R1：Escape 关闭并归还焦点（回到打开它的按钮所在弹窗）
    await page.keyboard.press('Escape')
    await expect(page.getByTestId('legal-modal')).toHaveCount(0)
    await expect(page.getByTestId('invite-register')).toBeVisible()
  })

  test('邀请链接打开注册弹窗并预填邀请码', async ({ page }) => {
    await page.goto('/?invite=invite-abc')
    await expect(page.getByTestId('invite-register')).toBeVisible()
    await expect(page.getByTestId('invite-code-input')).toHaveValue('invite-abc')
    await page.getByTestId('invite-close').click()
    await expect(page.getByTestId('invite-register')).toHaveCount(0)
  })

  test('上传失败可重试，成功后入库', async ({ page }) => {
    let calls = 0
    await page.route('**/api/rag/ingest', (route) => {
      calls += 1
      if (calls === 1) {
        return route.fulfill({ status: 500, json: { detail: { code: 'x', message: '服务器繁忙' } } })
      }
      return route.fulfill({ status: 200, json: { source: 'retry.md', chunks: 2, doc_id: 'local:r' } })
    })
    await page.route('**/api/rag/docs', (route) => route.fulfill({ json: { docs: [] } }))

    await page.getByTestId('rag-upload-input').setInputFiles({
      name: 'retry.md',
      mimeType: 'text/markdown',
      buffer: Buffer.from('# retry'),
    })
    const row = page.getByTestId('upload-item').filter({ hasText: 'retry.md' })
    await expect(row).toContainText('失败')

    await row.getByTestId('upload-retry').click()
    await expect(row).toContainText('已入库（2 块）')
  })

  test('历史分页失败保留已加载列表并可重试', async ({ page }) => {
    let failNextPage = true
    await page.route('**/api/runs*', async (route) => {
      const url = new URL(route.request().url())
      const offset = Number(url.searchParams.get('offset') ?? '0')
      if (offset > 0 && failNextPage) {
        failNextPage = false
        return route.fulfill({ status: 500, json: { detail: { code: 'x', message: '服务器繁忙' } } })
      }
      const runs = Array.from({ length: 10 }, (_, index) => ({
        run_id: `run${String(offset + index).padStart(10, '0')}`,
        topic: `任务 ${offset + index}`,
        status: 'SUCCEEDED',
        stop_reason: 'completed',
        created_at: null,
        has_report: false,
      }))
      return route.fulfill({ json: { runs, limit: 10, offset } })
    })

    await page.getByTestId('history-toggle').click()
    await expect(page.getByTestId('history-panel').getByText('任务 0')).toBeVisible()
    await page.getByTestId('history-load-more').click()

    // R3：翻页失败不清空已加载列表，给出内联重试
    await expect(page.getByTestId('history-append-error')).toBeVisible()
    await expect(page.getByTestId('history-panel').getByText('任务 0')).toBeVisible()
    await page.getByTestId('history-retry').click()
    await expect(page.getByTestId('history-panel').getByText('任务 10')).toBeVisible()
  })

  test('邀请参数一次性消费，刷新不再重开注册弹窗', async ({ page }) => {
    await page.goto('/?invite=invite-abc')
    await expect(page.getByTestId('invite-register')).toBeVisible()
    await page.getByTestId('invite-close').click()
    await page.reload()
    await expect(page.getByTestId('invite-register')).toHaveCount(0)
  })

  test('历史列表支持状态筛选、分页与报告预览（mock 后端）', async ({ page }) => {
    const calls: string[] = []
    await page.route('**/api/runs*', async (route) => {
      const url = new URL(route.request().url())
      calls.push(url.search)
      const offset = Number(url.searchParams.get('offset') ?? '0')
      const status = url.searchParams.get('status')
      const runs =
        status === 'FAILED'
          ? [{ run_id: 'fail00000001', topic: '失败的任务', status: 'FAILED', stop_reason: 'error', created_at: '2026-09-26T10:00:00', has_report: false }]
          : Array.from({ length: 10 }, (_, i) => ({
              run_id: `run${String(offset + i).padStart(10, '0')}`,
              topic: `任务 ${offset + i}`,
              status: 'SUCCEEDED',
              stop_reason: 'completed',
              created_at: '2026-09-26T10:00:00',
              has_report: i === 0,
              moderation_status: i === 0 ? 'flagged' : null,
            }))
      await route.fulfill({ json: { runs, limit: 10, offset } })
    })
    await page.route('**/api/research/run0000000000/report*', (route) =>
      route.fulfill({ body: '# 历史报告正文', contentType: 'text/markdown' }),
    )

    await page.getByTestId('history-toggle').click()
    await expect(page.getByTestId('history-panel').getByText('任务 0')).toBeVisible()
    await expect(page.getByTestId('history-panel').getByTestId('flagged-badge')).toBeVisible()
    await page.getByTestId('history-load-more').click()
    await expect(page.getByTestId('history-panel').getByText('任务 10')).toBeVisible()

    await page.getByTestId('history-status-filter').selectOption('FAILED')
    await expect(page.getByTestId('history-panel').getByText('失败的任务')).toBeVisible()
    expect(calls.some((search) => search.includes('status=FAILED'))).toBe(true)

    await page.getByTestId('history-status-filter').selectOption('')
    await expect(page.getByTestId('history-panel').getByText('任务 0')).toBeVisible()
    await page.getByRole('button', { name: '查看报告' }).first().click()
    await expect(page.getByTestId('history-preview')).toContainText('历史报告正文')
    await page.getByRole('button', { name: '关闭' }).click()
    await expect(page.getByTestId('history-preview')).toHaveCount(0)
  })

  test('邀请注册弹窗可打开隐私政策与用户协议', async ({ page }) => {
    await page.goto('/?invite=invite-abc')
    await page.getByTestId('invite-register').getByRole('button', { name: '隐私政策' }).click()
    await expect(page.getByTestId('legal-modal')).toContainText('隐私政策')
    await expect(page.getByTestId('legal-modal')).toContainText('数据存在哪里')
    await page.getByTestId('legal-close').click()
    await expect(page.getByTestId('legal-modal')).toHaveCount(0)

    await page.getByTestId('invite-register').getByRole('button', { name: '用户协议' }).click()
    await expect(page.getByTestId('legal-modal')).toContainText('使用规范')
    await page.getByTestId('legal-close').click()
  })
})
