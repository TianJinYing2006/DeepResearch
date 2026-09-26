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

  // ---- P6-B：邀请链接 / 历史筛选与分页 ----

  test('邀请链接打开注册弹窗并预填邀请码', async ({ page }) => {
    await page.goto('/?invite=invite-abc')
    await expect(page.getByTestId('invite-register')).toBeVisible()
    await expect(page.getByTestId('invite-code-input')).toHaveValue('invite-abc')
    await page.getByTestId('invite-close').click()
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
