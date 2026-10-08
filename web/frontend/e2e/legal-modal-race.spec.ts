import { expect, test } from '@playwright/test'

/** 法律弹窗的**在途响应竞态**（需求 34）。
 *
 * 这个用例是**确定性复现**，不依赖机器负载 —— 它把 ``/api/legal/*`` 的响应扣在路由层，
 * 使「关闭发生在响应到达之前」这个时序完全可控。
 *
 * 缺陷形态（修复前）：``open()`` 在 ``await fetch`` 之后**无条件** ``setLegal(...)``。
 * 用户在请求在途时关闭弹窗 ⇒ 迟到的响应把弹窗**重新打开**。
 *
 * 为什么它在 CI 上表现为间歇性失败、本机却复现不了：
 * 原用例用 ``toHaveCount(0)`` 轮询，间隔约 100ms。关闭与重开若落在同一个轮询间隔内，
 * 中间计数为 0 的瞬间会被**跳过**，断言于是持续看到 1 而超时失败
 * （本会话 PR #160 的 e2e 偶发失败即此：重跑通过、本机三跑全过、代码却确实有缺陷）。
 *
 * 修复：``useLegalDoc`` 加**代次守卫** —— ``close()`` 递增代次使在途请求失效，
 * 响应回来时若代次已变则丢弃。用代次而非 ``cancelled`` 布尔量，
 * 是因为「关掉 A 又立刻打开 B」时后者也必须胜出。
 *
 * ⚠️ 断言必须落在**放行响应之后**：只在关闭时断言 0 是测不出这个缺陷的
 * （修复前那一瞬间确实是 0）。 */
test.describe('法律弹窗：在途响应不得把已关闭的弹窗重新打开', () => {
  test('关闭后放行迟到响应，弹窗不得重新出现', async ({ page }) => {
    let release = () => {}
    const held = new Promise<void>((resolve) => { release = resolve })

    await page.route('**/api/legal/privacy', async (route) => {
      await held                       // 扣住响应，直到测试放行
      await route.fulfill({ json: { markdown: '# 隐私政策正文' } })
    })

    await page.goto('/?invite=invite-abc')
    await page.getByTestId('invite-register').getByRole('button', { name: '隐私政策' }).click()
    await expect(page.getByTestId('legal-modal')).toBeVisible()

    // 响应仍在途中时关闭
    await page.keyboard.press('Escape')
    await expect(page.getByTestId('legal-modal')).toHaveCount(0)

    // 放行那个「迟到」的响应 —— 这一步才是真正的判据
    release()
    await page.waitForTimeout(600)
    await expect(page.getByTestId('legal-modal')).toHaveCount(0)
    // 关闭后焦点应归还给打开它的那个弹窗，且邀请弹窗不受影响
    await expect(page.getByTestId('invite-register')).toBeVisible()
  })

  test('在途时改开另一份文档，先到者不得覆盖后到者', async ({ page }) => {
    // 代次守卫用「代次」而非布尔量的理由：连续两次 open 时，晚发起的必须胜出。
    await page.route('**/api/legal/privacy', (route) =>
      route.fulfill({ json: { markdown: '# 隐私政策正文（慢）' } }),
    )
    await page.route('**/api/legal/terms', (route) =>
      route.fulfill({ json: { markdown: '# 用户协议正文（快）' } }),
    )

    await page.goto('/?invite=invite-abc')
    await page.getByTestId('invite-register').getByRole('button', { name: '隐私政策' }).click()
    await page.getByTestId('legal-close').click()
    await page.getByTestId('invite-register').getByRole('button', { name: '用户协议' }).click()

    await expect(page.getByTestId('legal-modal')).toContainText('用户协议')
    await expect(page.getByTestId('legal-modal')).toContainText('用户协议正文（快）')
  })
})
