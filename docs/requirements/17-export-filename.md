# 需求 17：导出 .md 文件名改为话题名（RFC 6266 双格式）

> 状态：**草稿**（随 PR 合入后置「已合」）；实施进度以 `docs/project-status.md` 为唯一看板（D-01）。
> 来源：L3-A 内测反馈（2026-09-30）：导出文件名是一串 run_id，识别度差。

## 1. 元信息

| 项 | 值 |
|---|---|
| 编号 | 17 |
| 标题 | 导出 .md 文件名改为话题名 |
| 优先级 | P2（体验优化） |
| 状态 | 草稿 |
| 负责人 | TianJinYing2006 |
| 关联 Issue | #77 |
| 关联 PR | 本 PR |
| 创建 / 更新 | 2026-09-30 |

## 2. 问题背景

导出文件名为固定 `deepresearch-<run_id>.md`（`main.py` 两处硬编码），且前端 blob
下载再硬编码一次（`App.tsx`），后端 `Content-Disposition` 被覆盖；用户无法从文件名识别报告内容。

## 3. 需求分析

- 文件名 = 话题名（中文正确显示），空 / 非法话题回退 `deepresearch-<run_id>`；
- 清洗：去路径（`../` 等）、控制字符、`\/:*?"<>|`；截断 ≤50 字符；
- **不改**导出内容、审计元数据与 flagged/blocked 403 闸；`format=json` 保持内联不变。

## 4. 当前设计（代码位置）

- 后端：`web/backend/main.py` `_export_from_store` / `export_report` 两处
  `Content-Disposition: attachment; filename="deepresearch-<run_id>.md"`；
- 前端：`web/frontend/src/App.tsx` `exportReport()` blob 下载 `anchor.download = "deepresearch-<runId>.md"`（覆盖后端头）；
- 可复用清洗：`web/backend/upload_guard.py:sanitize_filename()`（上传面在用）。

## 5. 优化方案（业内口径：RFC 6266 / RFC 5987）

- 新增 `web/backend/download_names.py`：
  - `build_export_filename(topic, run_id, ext=".md") -> (display, ascii_fallback)`；
  - `content_disposition(display, ascii_fallback)`：**`filename`（ASCII 回退）在前、
    `filename*`（UTF-8 percent-encoded）在后**（RFC 6266 §Appendix D：UA 优先 filename*，
    旧 UA 退回 ASCII；避免裸非 ASCII 的 ISO-8859-1 解码歧义）；
- `main.py` 两条导出路径统一走 `_markdown_export_response()`；
- 前端 `lib/download.ts` `filenameFromDisposition()` 解析响应头（`filename*` 优先，
  `filename` 回退），`App.tsx` 不再自造文件名；
- 话题来源：内存路径 `payload.topic`；store 回落路径 `row.topic`。

## 6. 设计策略

- 服务端单点产出文件名（前端只消费），避免前后端各写一份清洗逻辑；
- ASCII 回退名固定由服务端生成（不含用户输入），无注入面；
- 复用上传面的 `sanitize_filename`（同一套「不可信文件名」清洗口径）。

## 7. 验收标准（DoD）

- [x] 后端 Content-Disposition 使用清洗后话题名（UTF-8、ASCII 回退可用）
- [x] 前端 blob 下载名读取响应头（缺失回退 run_id）
- [x] 清洗单测：中文 / emoji 类非法字符 / 路径穿越 / 超长 / 空主题回退
- [x] 契约测试：`GET /api/research/{id}/report?format=md` 头部文件名断言
- [x] E2E 用例更新（`research.spec.ts`：断言下载名 = 话题名）
- [ ] CI 全量（lint-and-test + infra + frontend + e2e）零回归

## 8. 影响范围与风险

- 模块：`web/backend/download_names.py`（新）、`main.py`、`web/frontend/src/lib/download.ts`（新）、
  `App.tsx`、`tests/test_export_filename.py`（新）、`tests/test_web_guardrails.py`、`e2e/research.spec.ts`；
- 风险：极低——仅文件名与头部格式；浏览器对 `filename*` 支持广泛，旧 UA 走 ASCII 回退；
- 兜底：清洗后为空 → `deepresearch-<run_id>`。

## 9. 测试策略

- 单测：`tests/test_export_filename.py`（7 条：中文 / ASCII / 路径穿越 / 非法字符 / 空值回退 / 截断 / RFC 头部形状）；
- 契约：`tests/test_web_guardrails.py::test_export_filename_uses_topic`；
- E2E：文档导出用例断言下载名（CI `e2e` job 复核）。

## 10. 变更记录

| 日期 | 类型 | 原因 | 改动摘要 | 关联 PR/commit |
|---|---|---|---|---|
| 2026-09-30 | 优化 | 内测反馈：导出文件名识别度差（Issue #77） | 话题名 + RFC 6266 双格式 + 前端解析 | 本 PR |
