import { expect, test } from '@playwright/test'

// P6-A：账号/知识库入口在「未配置任务库 / 无 Qdrant」时的降级表现，以及上传类型校验。
// 鉴权开启后的登录门 E2E 需要真实任务库，暂由后端用例（tests/test_rag_api.py、test_auth_api.py）覆盖。
test.describe('账号与知识库入口（桌面端）', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/')
  })

  test('未配置任务库时历史入口给出结构化提示', async ({ page }) => {
    await page.getByTestId('history-toggle').click()
    await expect(page.getByTestId('history-error')).toContainText('未配置任务库')
  })

  test('账号条与上传入口可用，且不影响主流程', async ({ page }) => {
    await expect(page.getByTestId('account-panel')).toBeVisible()
    await expect(page.getByTestId('rag-upload-input')).toBeAttached()
    await expect(page.getByRole('button', { name: /开始研究/ })).toBeVisible()
  })

  test('上传不支持的文件类型被明确拒绝', async ({ page }) => {
    await page.getByTestId('rag-upload-input').setInputFiles({
      name: 'evil.exe',
      mimeType: 'application/octet-stream',
      buffer: Buffer.from('not-a-document'),
    })
    await expect(page.getByTestId('upload-state')).toContainText('不支持的文件类型')
  })
})
