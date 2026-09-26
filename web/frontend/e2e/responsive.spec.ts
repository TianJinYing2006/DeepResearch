import { expect, test } from '@playwright/test'

import { settleRun } from './helpers'

/** 移动端适配（P1-7）：窄屏**不得出现横向滚动**。

 这是能客观判定的一条：宽表 / 长 URL / 代码块是报告页面最常见的三种撑破布局的元素，
 判据统一成 `scrollWidth <= clientWidth`，不靠肉眼看截图。 */
test.describe('移动端布局', () => {
  test.afterEach(async ({ page }) => {
    await settleRun(page)
  })

  test('iPhone 级窄屏下表单与报告都不产生横向滚动', async ({ page }) => {
    await page.goto('/')
    await expect(page.locator('#topic')).toBeVisible()

    const noOverflow = async (label: string) => {
      const overflow = await page.evaluate(() => ({
        scrollWidth: document.documentElement.scrollWidth,
        clientWidth: document.documentElement.clientWidth,
      }))
      expect(overflow.scrollWidth, `${label} 出现横向滚动`).toBeLessThanOrEqual(overflow.clientWidth + 1)
    }

    await noOverflow('表单页')

    await page.fill('#topic', '窄屏报告渲染')
    await page.click('button[type="submit"]')
    await expect(page.locator('[data-testid="report-heading"]')).toBeVisible({ timeout: 90_000 })

    await noOverflow('报告页（含宽表与长链接）')

    // 宽表**没被裁掉**：它仍在 DOM 里，且被可横向滚动的容器包着（不是被裁掉看不见）
    await expect(page.locator('article.report-prose div.overflow-x-auto > table')).toHaveCount(1)
    // 超长 URL 也要在窄屏内换行显示，而不是把页面顶宽
    const longLink = page.locator('article.report-prose a[href^="https://example.com/industry-reports"]')
    await expect(longLink).toHaveCount(1)
    const linkBox = await longLink.boundingBox()
    const viewport = page.viewportSize()
    expect(linkBox).not.toBeNull()
    expect(linkBox!.width).toBeLessThanOrEqual(viewport!.width)
  })

  // P6-B：账号条与历史面板（含筛选/分页行）在窄屏同样不得撑破布局
  test('窄屏下账号条与历史面板不产生横向滚动', async ({ page }) => {
    await page.goto('/')
    await expect(page.getByTestId('account-panel')).toBeVisible()

    const noOverflow = async (label: string) => {
      const overflow = await page.evaluate(() => ({
        scrollWidth: document.documentElement.scrollWidth,
        clientWidth: document.documentElement.clientWidth,
      }))
      expect(overflow.scrollWidth, `${label} 出现横向滚动`).toBeLessThanOrEqual(overflow.clientWidth + 1)
    }

    await noOverflow('账号条')

    await page.getByTestId('history-toggle').click()
    await expect(page.getByTestId('history-panel')).toBeVisible()
    await noOverflow('历史面板')
  })
})
