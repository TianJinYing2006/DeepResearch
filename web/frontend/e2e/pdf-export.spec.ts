import { readFileSync } from 'node:fs'

import { expect, test } from '@playwright/test'

import { settleRun } from './helpers'

/** 需求 38 F2：PDF 异步导出（桌面端）。

 DR_DEMO=1 下后端挂载内存替身（`demo_export.py`）：POST 创建 → 轮询状态 → 授权下载
 走**同一条客户端状态机**；PDF bytes 为静态最小样本（真实渲染由 export 服务与 CI 渲染测试覆盖）。 */
test.describe('PDF 异步导出（需求 38 F2）', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/')
  })

  test.afterEach(async ({ page }) => {
    await settleRun(page)
  })

  test('创建作业→轮询→下载，文件名沿用话题名', async ({ page }) => {
    await page.fill('#topic', 'PDF 导出验证')
    await page.click('button[type="submit"]')
    await expect(page.locator('[data-testid="report-heading"]')).toBeVisible({ timeout: 90_000 })

    const [download] = await Promise.all([
      page.waitForEvent('download', { timeout: 60_000 }),
      page.click('[data-testid="export-pdf-button"]'),
    ])
    // 需求 17/38：文件名 = 话题名（RFC 6266 filename*），扩展名 .pdf
    expect(download.suggestedFilename()).toBe('PDF 导出验证.pdf')

    const path = await download.path()
    expect(path).toBeTruthy()
    const bytes = readFileSync(path as string)
    expect(bytes.subarray(0, 5).toString('ascii')).toBe('%PDF-')

    // 按钮回到可用态（已导出 → 定时复位），期间不得残留「生成中」死锁
    await expect(page.getByTestId('export-pdf-button')).toBeEnabled({ timeout: 10_000 })
  })

  test('同一 run 重复申请复用既有作业（reused）', async ({ page }) => {
    await page.fill('#topic', 'PDF 幂等验证')
    await page.click('button[type="submit"]')
    await expect(page.locator('[data-testid="report-heading"]')).toBeVisible({ timeout: 90_000 })

    const [download] = await Promise.all([
      page.waitForEvent('download', { timeout: 60_000 }),
      page.click('[data-testid="export-pdf-button"]'),
    ])
    expect(download.suggestedFilename()).toBe('PDF 幂等验证.pdf')

    // run_id 从会话存储取（终局后保留；CI demo 环境无任务库）
    const runId = await page.evaluate(() => sessionStorage.getItem('dr.lastRunId') ?? '')
    expect(runId).toMatch(/^[a-f0-9]{6,}$/)
    const again = await page.request.post(`/api/research/${runId}/export-jobs`, {
      data: { kind: 'pdf' },
    })
    expect(again.ok()).toBeTruthy()
    const body = (await again.json()) as { reused: boolean; status: string }
    expect(body.reused).toBe(true)
    expect(body.status).toBe('SUCCEEDED')
  })
})
