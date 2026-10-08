import { expect, test, type Page } from '@playwright/test'

import { settleRun } from './helpers'

/** 窄屏流程回归（验收点④）：登录 / 上传 / 历史预览 / 长报告。
 *
 * 为什么单开一个 spec：
 * 移动端 project 原先只跑 `responsive.spec.ts` 两条**布局**用例（判据是「无横向滚动」）。
 * 布局不溢出 ≠ 流程能用 —— 窄屏下按钮可能换行到视野外、弹窗可能高过视口、
 * 面板可能被遮挡，而这些都是 `scrollWidth <= clientWidth` 查不出来的。
 *
 * 本 spec 只跑**窄屏视口**：它的断言前提就是窄屏本身，在桌面上重复跑等于
 * 把已有的桌面用例再做一遍（配置里用 `testIgnore` 排除，而不是用 skip —— 见 playwright.config.ts 的说明）。
 *
 * 「异常态」（断线恢复 / 取消中 / 配额耗尽）与视口无关，放在 `faults.spec.ts`，两个视口都跑。
 */
test.describe('窄屏流程回归', () => {
  test.afterEach(async ({ page }) => {
    await settleRun(page)
  })

  test('登录流程在窄屏可用', async ({ page }) => {
    await page.route('**/api/options', (route) =>
      route.fulfill({
        json: {
          search_providers: [],
          default_provider: 'bocha',
          enable_arxiv_default: false,
          max_total_hops_default: 2,
          max_subquestions_default: 2,
          run_timeout_seconds: 3,
          auth_required: true,
          invite_only: true,
        },
      }),
    )
    await page.route('**/api/auth/session', (route) =>
      route.fulfill({ status: 401, json: { detail: { code: 'unauthenticated', message: '未登录' } } }),
    )
    await page.route('**/api/auth/login', (route) =>
      route.fulfill({ json: { user: { user_id: 'u-demo', email: 'demo@example.com' } } }),
    )

    await page.goto('/')
    await page.getByTestId('landing-cta').click()

    const gate = page.getByTestId('auth-gate')
    await expect(gate).toBeVisible()
    // 窄屏判据：登录表单的两个输入框都必须落在视口宽度内（而非被撑到视野外）
    await expectWithinViewport(page, gate.locator('#auth-email'), '邮箱输入框')
    await expectWithinViewport(page, gate.locator('#auth-password'), '密码输入框')

    await gate.locator('#auth-email').fill('demo@example.com')
    await gate.locator('#auth-password').fill('password1234')
    await gate.getByRole('button', { name: '登录', exact: true }).click()

    await expect(page.getByTestId('account-email')).toHaveText('demo@example.com')
    await expect(page.getByTestId('auth-gate')).toHaveCount(0)
  })

  test('上传入口与队列在窄屏可用', async ({ page }) => {
    await page.route('**/api/rag/ingest', (route) =>
      route.fulfill({ status: 200, json: { source: 'narrow.md', chunks: 2, doc_id: 'local:narrow' } }),
    )
    await page.route('**/api/rag/docs', (route) => route.fulfill({ json: { docs: [] } }))
    await page.goto('/')

    const input = page.getByTestId('rag-upload-input')
    await expectWithinViewport(page, page.getByTestId('kb-toggle'), '知识库按钮')

    // 与 closure.spec 同口径：不依赖原生 filechooser（headless 下有约 20% 概率不触发的竞态）
    await input.setInputFiles({ name: 'narrow.md', mimeType: 'text/markdown', buffer: Buffer.from('# kb') })

    const item = page.getByTestId('upload-item').filter({ hasText: 'narrow.md' })
    await expect(item).toContainText('已入库（2 块）')
    // 队列行在窄屏不得溢出 —— 上传进度条是最容易撑破窄屏的元素之一
    await expectWithinViewport(page, item, '上传队列行')
    await noHorizontalOverflow(page, '上传后的账号条')
  })

  test('历史面板与报告预览在窄屏可用', async ({ page }) => {
    await page.route('**/api/runs*', (route) =>
      route.fulfill({
        json: {
          runs: [
            {
              run_id: 'run0000000000',
              topic: '窄屏历史任务',
              status: 'SUCCEEDED',
              stop_reason: 'completed',
              created_at: '2026-09-26T10:00:00',
              has_report: true,
            },
          ],
          limit: 10,
          offset: 0,
        },
      }),
    )
    await page.route('**/api/research/run0000000000/report*', (route) =>
      route.fulfill({ body: '# 窄屏历史报告正文', contentType: 'text/markdown' }),
    )

    await page.goto('/')
    await page.getByTestId('history-toggle').click()

    const panel = page.getByTestId('history-panel')
    await expect(panel.getByText('窄屏历史任务')).toBeVisible()
    await expectWithinViewport(page, panel, '历史面板')

    await page.getByRole('button', { name: '查看报告' }).first().click()
    const preview = page.getByTestId('history-preview')
    await expect(preview).toContainText('窄屏历史报告正文')
    // 预览弹窗在窄屏不得高过视口导致关闭按钮不可达
    await expectWithinViewport(page, preview, '历史预览弹窗')

    await preview.getByRole('button', { name: '关闭' }).click()
    await expect(preview).toHaveCount(0)
  })

  test('长报告在窄屏可读，引用核查与导出入口均可达', async ({ page }) => {
    await page.goto('/')
    await page.fill('#topic', '窄屏长报告与引用核查')
    await page.click('button[type="submit"]')
    await expect(page.getByTestId('report-heading')).toBeVisible({ timeout: 90_000 })

    // 引用核查：页边证据栏在窄屏落到报告下方，仍必须可见
    const evidence = page.locator('section[aria-label="引用证据"]')
    await expect(evidence).toBeVisible()
    await expect(evidence).toContainText('引用校验')

    // 导出 / 复制按钮会换行 —— 换行到视野外是「布局不溢出」查不出的典型窄屏故障。
    // 刻意不断言「分享」：它受后端 `options.share_enabled` 开关控制，DR_DEMO 下未开启。
    await expectWithinViewport(page, page.getByTestId('export-button'), '导出按钮')
    await expectWithinViewport(page, page.getByRole('button', { name: '复制正文' }), '复制按钮')

    await noHorizontalOverflow(page, '长报告页')
  })
})

/** 窄屏硬判据：元素左右边缘都必须落在视口宽度内。
 *
 * 与 `scrollWidth <= clientWidth` 互补 —— 后者查「页面被撑宽」，
 * 本函数查「元素被挤出视野」（例如 `overflow-hidden` 裁剪、或定位到视口之外），
 * 两者都成立才算窄屏真的可用。 */
async function expectWithinViewport(page: Page, locator: ReturnType<Page['locator']>, label: string) {
  const box = await locator.boundingBox()
  const viewport = page.viewportSize()
  expect(box, `${label} 应有可测量的包围盒`).not.toBeNull()
  expect(viewport, '窄屏用例必须有视口尺寸').not.toBeNull()
  expect(box!.x, `${label} 左边缘超出视口`).toBeGreaterThanOrEqual(-1)
  expect(box!.x + box!.width, `${label} 右边缘超出视口`).toBeLessThanOrEqual(viewport!.width + 1)
}

async function noHorizontalOverflow(page: Page, label: string) {
  const size = await page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    clientWidth: document.documentElement.clientWidth,
  }))
  expect(size.scrollWidth, `${label} 出现横向滚动`).toBeLessThanOrEqual(size.clientWidth + 1)
}
