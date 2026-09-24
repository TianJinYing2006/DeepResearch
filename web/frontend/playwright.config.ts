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
    // 流程 / 错误卡用例只在桌面视口跑；窄屏布局用例**两个视口都跑**。
    // ⚠️ 用 testMatch 分流，而不是在 beforeEach 里 test.skip()：
    //    skip 掉的用例仍会执行 afterEach（页面可能尚未导航）⇒ 清理钩子会挂死。
    {
      name: 'desktop',
      use: { ...devices['Desktop Chrome'], channel: CHANNEL },
      testMatch: /.*\.spec\.ts/,
    },
    {
      name: 'mobile',
      use: { ...devices['Pixel 5'], channel: CHANNEL },
      testMatch: /responsive\.spec\.ts/,
    },
  ],
  webServer: {
    // DR_DEMO=1 ⇒ 全程假数据，不调 LLM、不花钱；但**走的是同一条 SSE 管线**，
    // 因此事件序列、降级推送、取消、导出都是真的。
    command: `"${PYTHON}" -m uvicorn web.backend.main:app --host 127.0.0.1 --port ${PORT}`,
    cwd: repoRoot(),
    env: {
      DR_DEMO: '1',
      // 演示单节点耗时压到 0.4s ⇒ 一场演示 ~4.5s，而不是默认的 ~20s
      DR_DEMO_STEP_SECONDS: process.env.DR_DEMO_STEP_SECONDS ?? '0.4',
      OTEL_SDK_DISABLED: 'true',
      PYTHONIOENCODING: 'utf-8',
    },
    url: `${BASE_URL}/api/health`,
    reuseExistingServer: true,
    timeout: 120_000,
  },
})
