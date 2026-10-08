import { existsSync } from 'node:fs'
import path from 'node:path'

import { defineConfig, devices } from '@playwright/test'

/** 从当前目录向上找仓库根（以 pyproject.toml 为锚）。

 为什么不能写死相对路径：npm script 可能在 `web/frontend` 里跑，
 也可能有人从仓库根直接 `npx playwright test` ⇒ 相对路径会指向错的地方。 */
function repoRoot(): string {
  let dir = process.cwd()
  for (let depth = 0; depth < 5; depth += 1) {
    if (existsSync(path.join(dir, 'pyproject.toml'))) return dir
    dir = path.dirname(dir)
  }
  return process.cwd()
}

const PORT = Number(process.env.E2E_PORT ?? 8000)
const BASE_URL = process.env.E2E_BASE_URL ?? `http://127.0.0.1:${PORT}`
// 用**本机已装的** Chrome / Edge：不下载浏览器（离线环境、CI 无网时也能跑）。
// 想换浏览器：E2E_CHANNEL=msedge npm run e2e
const CHANNEL = process.env.E2E_CHANNEL ?? 'chrome'
// 后端解释器：必须是装了 requirements-lock.txt 的那个 venv（系统 python 3.14 缺 langgraph）
const PYTHON = process.env.DR_PYTHON ?? 'python'

export default defineConfig({
  testDir: './e2e',
  // 演示图 11 个节点 × 1.8s ≈ 20s，再加导出/重试等交互 ⇒ 单用例留足 2 分钟
  timeout: 120_000,
  expect: { timeout: 30_000 },
  // 后端是**单进程、并发上限 1**（P1-3）⇒ 用例必须串行，否则第二个会被 429 拒掉
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [['list']],
  use: {
    baseURL: BASE_URL,
    channel: CHANNEL,
    trace: 'off',
    video: 'off',
  },
  projects: [
    // 分流规则（验收点④ 扩充后）：
    // - `responsive` / `faults`：判据与视口正交（分别是布局、状态机）⇒ **两个视口都跑**；
    // - `mobile-flows`：断言前提就是窄屏本身（按钮是否被挤出视野、弹窗是否高过视口）
    //   ⇒ **只跑移动端**，桌面上重跑等于把已有的桌面用例再做一遍；
    // - 其余流程 / 错误卡用例：只在桌面视口跑。
    // ⚠️ 用 testMatch / testIgnore 分流，而不是在 beforeEach 里 test.skip()：
    //    skip 掉的用例仍会执行 afterEach（页面可能尚未导航）⇒ 清理钩子会挂死。
    {
      name: 'desktop',
      use: { ...devices['Desktop Chrome'], channel: CHANNEL },
      testMatch: /.*\.spec\.ts/,
      testIgnore: /mobile-flows\.spec\.ts/,
    },
    {
      name: 'mobile',
      use: { ...devices['Pixel 5'], channel: CHANNEL },
      testMatch: /(responsive|mobile-flows|faults)\.spec\.ts/,
    },
  ],
  webServer: {
    // DR_DEMO=1 ⇒ 全程假数据，不调 LLM、不花钱；但**走的是同一条 SSE 管线**，
    // 因此事件序列、降级推送、取消、导出都是真的。
    //
    // ⚠️ 这个 server 由 FastAPI 托管 `web/frontend/dist/` —— 也就是说 e2e 跑的是**构建产物**，
    // 不是 src。改完源码不重新 build 就跑用例，会看到「全绿」但测的是旧包（本项目真实踩过）。
    // 因此 `npm run e2e` 已改为「先 build 再跑」；只想对当前 dist 快速迭代时用 `npm run e2e:only`。
    command: `"${PYTHON}" -m uvicorn web.backend.main:app --host 127.0.0.1 --port ${PORT}`,
    cwd: repoRoot(),
    env: {
      DR_DEMO: '1',
      // 演示单节点耗时压到 0.4s ⇒ 一场演示 ~4.5s，而不是默认的 ~20s
      DR_DEMO_STEP_SECONDS: process.env.DR_DEMO_STEP_SECONDS ?? '0.4',
      // 关掉登录/提交限流（与 tests/conftest.py 同口径）：29 条用例串行跑，默认
      // 10 次/分钟的提交闸会跨用例累计，把后面的用例假性拒成 rate_limited；
      // 限流语义本身由 tests/test_ratelimit.py 与 test_quotas.py 覆盖。
      DR_SUBMIT_RATE_PER_MINUTE: '0',
      DR_LOGIN_RATE_PER_MINUTE: '0',
      DR_LOGIN_ACCOUNT_RATE_PER_MINUTE: '0',
      OTEL_SDK_DISABLED: 'true',
      PYTHONIOENCODING: 'utf-8',
    },
    url: `${BASE_URL}/api/health`,
    reuseExistingServer: true,
    timeout: 120_000,
  },
})
