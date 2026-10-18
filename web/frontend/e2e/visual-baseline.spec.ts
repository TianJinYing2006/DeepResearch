import { expect, test } from '@playwright/test'

import { settleRun } from './helpers'

/** 截图基线（方向 B 视觉回归防线，docs/web-frontend-redesign-directions.md R7 遗留项）。
 *
 * 口径（轻量，有意收窄）：
 * - 仅桌面视口：mobile project 的 testMatch 不含本文件，天然只跑 desktop（1280x720）；
 * - 只收 3 屏：落地页首屏 / 工作台启动态 / 报告卡 / 页边证据栏；
 * - `animations: 'disabled'` 冻结全部 CSS 动效（rise 入场 / marquee / shimmer / 进度过渡）；
 * - 动态区用 mask 遮掉：顶部滚动进度条（landing-progress）。
 *
 * 稳定性设计（当初放弃截图基线的根因就是跨环境字体）：
 * - 金标准**必须在 Ubuntu + fonts-noto-cjk 下生成**（ci.yml e2e job 有固字步骤）；
 * - Windows / macOS 本地 `--update-snapshots` 的产物**不得提交**（系统字体不同，CI 必红）；
 * - demo 后端数据全是代码内固定夹具（报告正文 / 引用 / 计数），跨跑确定；
 * - 截图前把焦点从输入框移开（`#stage-report` 点一下），避开系统光标闪烁。
 *
 * 更新金标准（Linux 一键，产物直接落到本目录 `*-snapshots/`，确认后提交）：
 *   docker run --rm -v "<仓库根>:/work" -w /work mcr.microsoft.com/playwright:v1.63.0-noble bash -lc "
 *     apt-get update -qq && apt-get install -y -qq python3-venv libpango-1.0-0 libpangoft2-1.0-0 fonts-noto-cjk >/dev/null &&
 *     python3 -m venv /tmp/e2e && /tmp/e2e/bin/pip install -q -r requirements-lock.txt &&
 *     npm --prefix web/frontend ci --no-audit --no-fund &&
 *     npm --prefix web/frontend run build >/dev/null &&
 *     cd web/frontend && E2E_CHANNEL=chromium DR_PYTHON=/tmp/e2e/bin/python OTEL_SDK_DISABLED=true npx playwright test visual-baseline --update-snapshots"
 */

// 金标准按平台后缀命名（Playwright 默认行为）：本文件只提交 `-desktop-linux.png`。
// 非 Linux 平台（本地 Windows/macOS）比对时会找不到 `win32`/`darwin` 金标准而
// 「自动播种 + 判失败」⇒ 直接跳过；要改视觉先按下方 docker 命令在 Linux 重生成。
// CI 是 Linux（见 ci.yml e2e job 的 fonts-noto-cjk 步骤），会真实执行本文件。
test.skip(process.platform !== 'linux', '视觉基线金标准在 Linux 下生成；本地用文件头的 docker 命令重生成')

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

// 抗锯齿/渲染容差：金标准在 Linux + Playwright chromium 下生成；比对侧允许
// 0.5% 像素差 + 单像素色阈 0.2（吸收字体 hinting/AA 的平台微差），仍能抓住
// 布局位移、配色改动、元素增删这类真回归。
const SHOT = {
  animations: 'disabled' as const,
  threshold: 0.2,
  maxDiffPixelRatio: 0.005,
}

test.describe('视觉基线（桌面端）', () => {
  test.afterEach(async ({ page }) => {
    await settleRun(page)
  })

  test('落地页首屏', async ({ page }) => {
    await page.route('**/api/options', (route) => route.fulfill({ json: AUTH_OPTIONS }))
    await page.route('**/api/auth/session', (route) =>
      route.fulfill({ status: 401, json: { detail: { code: 'unauthenticated', message: '未登录' } } }),
    )

    await page.goto('/')
    await expect(page.getByTestId('landing-page')).toBeVisible()
    await expect(page.getByTestId('landing-cta')).toBeVisible()

    await expect(page).toHaveScreenshot('landing-hero.png', {
      ...SHOT,
      mask: [page.getByTestId('landing-progress')],
    })
  })

  test('工作台启动态', async ({ page }) => {
    // demo 默认免登录（与 research.spec 同口径，不拦截即进工作台）。
    await page.goto('/')
    await expect(page.locator('#topic')).toBeVisible()
    await expect(page.getByTestId('status-badge')).toBeVisible()

    await expect(page).toHaveScreenshot('workbench-idle.png', SHOT)
  })

  test('报告卡与页边证据栏', async ({ page }) => {
    await page.goto('/')
    await page.fill('#topic', '视觉基线演示主题')
    await page.click('button[type="submit"]')

    await expect(page.locator('[data-testid="report-heading"]')).toBeVisible({ timeout: 90_000 })
    await expect(page.getByTestId('status-badge')).toContainText('研究完成', { timeout: 30_000 })
    // 报告已整段替换完成（终局态），再把焦点移出输入框，避开光标。
    await page.locator('#stage-report').click()

    await expect(page.locator('#stage-report')).toHaveScreenshot('report-card.png', SHOT)
    await expect(page.locator('#stage-verify')).toHaveScreenshot('evidence-margin.png', SHOT)
  })
})
