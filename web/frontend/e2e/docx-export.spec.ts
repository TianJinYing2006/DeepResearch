import { readFileSync } from 'node:fs'

import { expect, test } from '@playwright/test'

import { settleRun } from './helpers'

/** 需求 38 F3：DOCX 同步导出（桌面端）。

 DR_DEMO=1 下走内存 manager 路径（与 store 路径同一装配与渲染管线）；
 断言下载名（RFC 6266 话题名）与 docx 的 zip 容器魔数；结构等价由
 `tests/test_docx_render.py` / `tests/test_docx_export_api.py` 锁定。 */
test.describe('DOCX 导出（需求 38 F3）', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/')
  })

  test.afterEach(async ({ page }) => {
    await settleRun(page)
  })

  test('同步下载，文件名沿用话题名', async ({ page }) => {
    await page.fill('#topic', 'DOCX 导出验证')
    await page.click('button[type="submit"]')
    await expect(page.locator('[data-testid="report-heading"]')).toBeVisible({ timeout: 90_000 })

    const [download] = await Promise.all([
      page.waitForEvent('download', { timeout: 30_000 }),
      page.click('[data-testid="export-docx-button"]'),
    ])
    expect(download.suggestedFilename()).toBe('DOCX 导出验证.docx')

    const path = await download.path()
    expect(path).toBeTruthy()
    const bytes = readFileSync(path as string)
    expect(bytes.subarray(0, 2).toString('ascii')).toBe('PK') // docx = zip 容器
  })
})
