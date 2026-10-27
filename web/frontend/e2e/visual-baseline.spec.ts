import { expect, test, type Page } from '@playwright/test'

import { settleRun } from './helpers'

/** 截图基线（方向 B 视觉回归防线，docs/web-frontend-redesign-directions.md R7 遗留项）。
 *
 * 口径（轻量，有意收窄）：
 * - 仅桌面视口：mobile project 的 testMatch 不含本文件，天然只跑 desktop（1280x720）；
 * - 只收 4 屏：落地页首屏 / 工作台启动态 / 报告卡 / 页边证据栏；
 * - `animations: 'disabled'` 冻结全部 CSS 动效（rise 入场 / marquee / shimmer / 进度过渡）；
 * - `reducedMotion: 'reduce'` 让 JS 动效也确定化（打字机 / 滚动视差 / reveal 扫描），
 *   见下方 `test.use` 说明 —— 这是落地页能被稳定截图的前提；
 * - 动态区用 mask 遮掉：顶部滚动进度条（landing-progress）。
 *
 * ⚠️ 金标准的**基准环境 = CI 的 e2e job**（`ubuntu-latest` + 官方 chromium + `fonts-noto-cjk`）。
 * 当初放弃截图基线的根因就是跨环境字体：同一份 UI 在本地 noble 容器与 CI runner 上，
 * 字体 hinting/fontconfig 版本略有差异 ⇒ 文本像素整体偏移（实测 3% 差，远超容差），
 * 所以金标准必须钉在**跑 CI 的那台机器**上，而不是随便一个 Linux。
 *
 * 稳定性设计：
 * - demo 后端数据全是代码内固定夹具（报告正文 / 引用 / 计数），跨跑确定；
 * - 截图前把焦点从输入框移开（`#stage-report` 点一下），避开系统光标闪烁；
 * - 容差 `threshold 0.2 + maxDiffPixelRatio 0.5%` 吸收同机 AA 微差。
 *
 * 更新金标准（唯一可靠路径：用 CI 环境自身的渲染）：
 *   1. 改完 UI 推 PR → e2e job 失败 → 从 Actions artifact `e2e-playwright-results`
 *      下载 test-results 目录下的 `*-actual.png`（即 CI 真实渲染）；
 *   2. 覆盖到本目录并改名：`<name>-actual.png` → `<name>-desktop-linux.png`；
 *   3. 提交。次轮 CI 即通过。
 *   （本机 Linux 可用下方 docker 命令近似生成，但与 CI 可能有字体微差，仅作预览。）
 *   docker run --rm -v "<仓库根>:/work" -w /work mcr.microsoft.com/playwright:v1.63.0-noble bash -lc "
 *     apt-get update -qq && apt-get install -y -qq python3-venv libpango-1.0-0 libpangoft2-1.0-0 fonts-noto-cjk >/dev/null &&
 *     python3 -m venv /tmp/e2e && /tmp/e2e/bin/pip install -q -r requirements-lock.txt &&
 *     npm --prefix web/frontend ci --no-audit --no-fund && npm --prefix web/frontend run build >/dev/null &&
 *     cd web/frontend && E2E_CHANNEL=chromium DR_PYTHON=/tmp/e2e/bin/python OTEL_SDK_DISABLED=true npx playwright test visual-baseline --update-snapshots"
 */

// 金标准按平台后缀命名（Playwright 默认行为）：本文件只提交 `-desktop-linux.png`。
// 非 Linux 平台（本地 Windows/macOS）比对时会找不到 `win32`/`darwin` 金标准而
// 「自动播种 + 判失败」⇒ 直接跳过。CI 是 Linux，会真实执行本文件。
test.skip(process.platform !== 'linux', '视觉基线金标准钉在 CI（Linux）环境；本地用文件头流程重生成')

const AUTH_OPTIONS = {
  search_providers: [],
  default_provider: 'bocha',
  enable_arxiv_default: false,
  max_total_hops_default: 2,
  max_subquestions_default: 2,
  run_timeout_seconds: 3,
  auth_required: true,
}

// 抗锯齿/渲染容差：金标准钉在 CI（Linux + 官方 chromium）环境；比对侧允许
// 0.5% 像素差 + 单像素色阈 0.2（吸收同机 AA 微差与字体 hinting 抖动），
// 仍能抓住布局位移、配色改动、元素增删这类真回归。
const SHOT = {
  animations: 'disabled' as const,
  threshold: 0.2,
  maxDiffPixelRatio: 0.005,
}

// ⚠️ 确定性关键：`reducedMotion: 'reduce'`。
// 落地页有 JS 驱动的「打字机」（逐字输出）、滚动视差与 reveal 扫描 —— 静态截图永远
// 抓不稳（实测每次截图像素数都在变）。而本应用的 `useTypewriter` / `useRevealAll` /
// hero 视差在 `prefers-reduced-motion: reduce` 下**全部瞬时确定化**（全量文本、无视差、
// reveal 直接可见），CSS 动效也被该媒体查询归零。
// ⇒ 截图基线钉在 reduced-motion 渲染上；动画态的正确性由功能 e2e 覆盖
//   （如 closure.spec 断言滚动驱动的工作流逐级激活），不靠静态像素。
test.use({ reducedMotion: 'reduce' })

/** 把元素高度钉成整数像素，使**元素截图的尺寸**确定。
 *
 *  为什么必须：`#stage-verify` 的 `max-h-[calc(100dvh-2rem)]` 在 CI 上取整会来回抖
 *  1px（688 ↔ 689），而截图**尺寸不符不走容差**、直接报 `size mismatch`（实测 dev push
 *  命中过一次，PR 轮侥幸通过）。这里显式设 height+maxHeight = 由视口推出的整数
 *  （与 CSS 公式 `100dvh - 2rem` 一致），宽度保持自然。 */
async function pinHeight(page: Page, selector: string, heightPx: number) {
  await page.locator(selector).evaluate((el, h) => {
    const node = el as HTMLElement
    node.style.height = `${h}px`
    node.style.maxHeight = `${h}px`
  }, heightPx)
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
    // 证据边栏：**主动滚入视口 + 固定尺寸 clip**。
    // ① 不用 locator 截图：其截图矩形由 boundingBox 取整而来，元素高度即使钉成 688 仍可能得到
    //    689（dev push 二次命中 688/689 的 size mismatch）；clip 把宽高写死 ⇒ 尺寸按构造确定。
    // ② 必须先自己滚入视口：`toHaveScreenshot` 内部滚动会改变 `xl:sticky` 元素的位置，
    //    导致裁剪区域与元素错位（实测漏此步 → 1% 像素差）。
    // ③ clip 用**视口坐标**（与 boundingBox 同系；文档坐标会导致「Clipped area is outside」）。
    await page.locator('#stage-verify').scrollIntoViewIfNeeded()
    await pinHeight(page, '#stage-verify', 688)
    const clip = await page.locator('#stage-verify').evaluate((el) => {
      const rect = el.getBoundingClientRect()
      return { x: Math.round(rect.left), y: Math.round(rect.top), width: 320, height: 688 }
    })
    await expect(page).toHaveScreenshot('evidence-margin.png', { ...SHOT, clip })
  })
})
