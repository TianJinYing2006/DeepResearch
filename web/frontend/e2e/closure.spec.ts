import { expect, test } from '@playwright/test'

/** R7 收口：补齐盘点 §7 的测试缺口（登录门 / 超时 / 配额 / 键盘上传 / KB 删除 / 注销）。

 全部用**路由拦截**构造确定性场景（与 error-handling.spec 同思路）：
 不依赖真实任务库 / Qdrant / LLM，也不占用后端并发闸。 */

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

test.describe('收口回归（桌面端）', () => {
  test('鉴权开启时登录门可切换注册并完成登录', async ({ page }) => {
    await page.route('**/api/options', (route) => route.fulfill({ json: AUTH_OPTIONS }))
    await page.route('**/api/auth/session', (route) =>
      route.fulfill({ status: 401, json: { detail: { code: 'unauthenticated', message: '未登录' } } }),
    )
    await page.route('**/api/auth/login', (route) =>
      route.fulfill({ json: { user: { user_id: 'u-demo', email: 'demo@example.com' } } }),
    )

    await page.goto('/')
    const gate = page.getByTestId('auth-gate')
    await expect(gate).toBeVisible()
    await expect(gate.getByRole('heading', { name: '登录 DeepResearch' })).toBeVisible()

    await gate.getByRole('button', { name: '有邀请码？去注册' }).click()
    await expect(gate.locator('#auth-invite')).toBeVisible()
    await gate.getByRole('button', { name: '已有账号？去登录' }).click()
    await expect(gate.locator('#auth-invite')).toHaveCount(0)

    await gate.locator('#auth-email').fill('demo@example.com')
    await gate.locator('#auth-password').fill('password1234')
    await gate.getByRole('button', { name: '登录', exact: true }).click()

    await expect(page.getByTestId('account-email')).toHaveText('demo@example.com')
    await expect(page.getByTestId('auth-gate')).toHaveCount(0)
  })

  test('登录门支持显示/隐藏密码', async ({ page }) => {
    await page.route('**/api/options', (route) => route.fulfill({ json: AUTH_OPTIONS }))
    await page.route('**/api/auth/session', (route) =>
      route.fulfill({ status: 401, json: { detail: { code: 'unauthenticated', message: '未登录' } } }),
    )

    await page.goto('/')
    const gate = page.getByTestId('auth-gate')
    const password = gate.locator('#auth-password')
    await expect(password).toHaveAttribute('type', 'password')
    await gate.getByTestId('auth-password-toggle').click()
    await expect(password).toHaveAttribute('type', 'text')
    await gate.getByTestId('auth-password-toggle').click()
    await expect(password).toHaveAttribute('type', 'password')
  })

  test('时限到点显示超时卡且不计为失败', async ({ page }) => {
    await page.route('**/api/research', async (route) => {
      if (route.request().method() !== 'POST') return route.fallback()
      return route.fulfill({ json: { run_id: 'timeoutdemo' } })
    })
    await page.route('**/api/research/timeoutdemo/stream', (route) => {
      const sse = [
        'id: 0\nevent: RUN_STARTED\ndata: {"type":"RUN_STARTED","run_id":"timeoutdemo","topic":"超时验证","max_total_hops":2,"timeout_seconds":1}\n\n',
        'id: 1\nevent: RUN_FINISHED\ndata: {"type":"RUN_FINISHED","cancelled":false,"stop_reason":"timeout","run_status":"degraded","token_used":0,"cost_estimate_cny":0,"degradation_count":0,"has_report":false,"result":{"report":"","citations":[],"validator_stats":{},"depth":0,"visited_sources":[],"reflection_log":[]}}\n\n',
      ].join('')
      return route.fulfill({ status: 200, contentType: 'text/event-stream', body: sse })
    })

    await page.goto('/')
    await page.fill('#topic', '超时验证')
    await page.click('button[type="submit"]')

    await expect(page.getByTestId('timeout-card')).toBeVisible()
    await expect(page.getByTestId('timeout-card')).toContainText('本次时限 1s')
    await expect(page.getByTestId('status-badge')).toContainText('已到时限停止')
    // 超时不是故障：不得出现错误卡
    await expect(page.getByTestId('error-card')).toHaveCount(0)
  })

  test('配额 chip 展示今日 / 并发 / 本月用量', async ({ page }) => {
    await page.route('**/api/quota', (route) =>
      route.fulfill({
        json: {
          daily_runs_used: 1,
          daily_runs_limit: 3,
          user_active_runs: 0,
          user_concurrent_limit: 1,
          run_budget_cny: 1.5,
          monthly_cost_cny: 0.5,
          monthly_budget_cny: 10,
        },
      }),
    )
    await page.goto('/')

    const chip = page.getByTestId('quota-chip')
    await expect(chip).toBeVisible()
    await expect(chip).toContainText('今日 1/3')
    await expect(chip).toContainText('并发 0/1')
    await expect(chip).toContainText('本月')
  })

  test('上传入口可 Tab 聚焦并用键盘唤起文件选择', async ({ page }) => {
    await page.route('**/api/rag/ingest', (route) =>
      route.fulfill({ status: 200, json: { source: 'kbd.md', chunks: 2, doc_id: 'local:kbd' } }),
    )
    await page.route('**/api/rag/docs', (route) => route.fulfill({ json: { docs: [] } }))
    await page.goto('/')

    // R7（审计 U1）：输入框进 Tab 序列（sr-only 而非 display:none）
    const input = page.getByTestId('rag-upload-input')
    let reached = false
    for (let index = 0; index < 12; index += 1) {
      await page.keyboard.press('Tab')
      reached = await input.evaluate((element) => element === document.activeElement)
      if (reached) break
    }
    expect(reached, '上传输入框应可通过 Tab 抵达').toBe(true)

    // 键盘激活 → 文件选择器 → 选择文件后走同一条上传链路
    const [chooser] = await Promise.all([
      page.waitForEvent('filechooser'),
      page.keyboard.press('Enter'),
    ])
    await chooser.setFiles({ name: 'kbd.md', mimeType: 'text/markdown', buffer: Buffer.from('# kb') })
    await expect(page.getByTestId('upload-item').filter({ hasText: 'kbd.md' }))
      .toContainText('已入库（2 块）')
  })

  test('知识库文档两步确认删除并即时刷新', async ({ page }) => {
    let docs = [
      { doc_id: 'local:del11111', source: '待删.pdf', chunks: 5 },
      { doc_id: 'local:keep2222', source: '保留.md', chunks: 2 },
    ]
    await page.route('**/api/rag/docs*', async (route) => {
      if (route.request().method() === 'DELETE') {
        const docId = new URL(route.request().url()).searchParams.get('doc_id')
        docs = docs.filter((doc) => doc.doc_id !== docId)
        return route.fulfill({ json: { ok: true, doc_id: docId } })
      }
      return route.fulfill({ json: { docs } })
    })

    await page.goto('/')
    await page.getByTestId('kb-toggle').click()
    await expect(page.getByTestId('kb-doc-item')).toHaveCount(2)

    const row = page.getByTestId('kb-doc-item').filter({ hasText: '待删.pdf' })
    await row.getByTestId('kb-delete').click()
    // 两步确认：出现「确认删除」，可取消
    await expect(row.getByTestId('kb-delete-confirm')).toBeVisible()
    await row.getByTestId('kb-delete-cancel').click()
    await expect(row.getByTestId('kb-delete')).toBeVisible()

    await row.getByTestId('kb-delete').click()
    await row.getByTestId('kb-delete-confirm').click()
    await expect(page.getByTestId('kb-doc-item')).toHaveCount(1)
    await expect(page.getByTestId('kb-doc-item')).toContainText('保留.md')
  })

  test('注销账号需密码确认并给出清理提示', async ({ page }) => {
    await page.route('**/api/options', (route) => route.fulfill({ json: AUTH_OPTIONS }))
    await page.route('**/api/auth/session', (route) =>
      route.fulfill({ json: { user: { user_id: 'u-bye', email: 'bye@example.com' } } }),
    )
    await page.route('**/api/auth/account', (route) =>
      route.fulfill({ json: { ok: true, deletion_request_id: 'del123', rag_cleanup: 'pending' } }),
    )
    page.on('dialog', (dialog) => void dialog.accept('password1234'))

    await page.goto('/')
    await expect(page.getByTestId('account-email')).toHaveText('bye@example.com')
    await page.getByTestId('delete-account').click()

    // 注销后回到登录门，并给出「知识库清理」提示（P0-7 outbox 语义）
    await expect(page.getByTestId('auth-gate')).toBeVisible()
    await expect(page.getByTestId('auth-notice')).toContainText('账号已注销')
    await expect(page.getByTestId('auth-notice')).toContainText('pending')
    await expect(page.getByTestId('account-email')).toHaveCount(0)
  })

  test('会话与安全：列出设备并退出其他所有设备', async ({ page }) => {
    await page.route('**/api/options', (route) => route.fulfill({ json: AUTH_OPTIONS }))
    await page.route('**/api/auth/session', (route) =>
      route.fulfill({ json: { user: { user_id: 'u-sec', email: 'sec@example.com' } } }),
    )
    let sessions = [
      {
        session_id: 's1',
        current: true,
        created_at: '2026-10-02T00:00:00Z',
        last_seen_at: '2026-10-02T01:00:00Z',
        ip: '1.1.1.1',
        user_agent: 'Mozilla/5.0 (Windows NT 10.0) Chrome/154.0',
      },
      {
        session_id: 's2',
        current: false,
        created_at: '2026-10-01T00:00:00Z',
        last_seen_at: '2026-10-01T01:00:00Z',
        ip: '2.2.2.2',
        user_agent: 'Mozilla/5.0 (iPhone) Safari/605.1',
      },
    ]
    await page.route('**/api/auth/sessions', async (route) => {
      if (route.request().method() === 'DELETE') {
        sessions = sessions.filter((item) => item.current)
        return route.fulfill({ json: { ok: true, revoked: 1 } })
      }
      return route.fulfill({ json: { sessions } })
    })

    await page.goto('/')
    await expect(page.getByTestId('account-email')).toHaveText('sec@example.com')
    await page.getByTestId('security-toggle').click()
    await expect(page.getByTestId('security-panel')).toBeVisible()
    await expect(page.getByTestId('session-item')).toHaveCount(2)
    await expect(page.getByTestId('session-current')).toHaveCount(1)
    await expect(page.getByTestId('security-panel')).toContainText('iPhone')

    // 退出其他设备需重认证（密码确认）→ 列表刷新为仅剩当前设备
    page.once('dialog', (dialog) => void dialog.accept('password1234'))
    await page.getByTestId('session-revoke-others').click()
    await expect(page.getByTestId('security-notice')).toContainText('已退出其他设备（1 个）')
    await expect(page.getByTestId('session-item')).toHaveCount(1)
  })
})
