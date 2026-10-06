# 前端结构盘点（重构范围真源）

> 状态：**现行**（2026-09-29 盘点，基线 `fca0bd7`）。
> 用途：前端系统性重构的**范围与边界真源**；与 `docs/requirements/12-frontend-ui-audit.md`（审计需求载体）配套。
> 实施进度以 `docs/project-status.md` 为唯一看板（D-01）。
> 硬边界：本文件只描述现状；重构不得破坏第 6 节的「行为契约」。

## 1. 文件清单

### 运行时（`web/frontend/src`，9 文件 / 2,707 行）

| 文件 | 行数 | 职责 |
|---|---:|---|
| `src/main.tsx` | 10 | React 入口；无路由、无 Provider |
| `src/App.tsx` | 952 | 整页骨架 + 主流程 UI + ~25 个模块级辅助组件/函数 |
| `src/components/AccountPanel.tsx` | 773 | 账号条 + 登录门 + 历史 + 知识库 + 4 个 portal 弹窗 + 11 个 API 调用 |
| `src/hooks/useResearchStream.ts` | 294 | SSE 生命周期 + 启动/恢复/取消 + 错误归一 + progress |
| `src/types/agui.ts` | 162 | AG-UI 事件/结果/错误/选项类型 + `isAguiEvent` |
| `src/lib/progress.ts` | 126 | 纯函数：5 阶段进度 + 事件流 summarize |
| `src/index.css` | 203 | 主题、滚动条、复用组件类、`report-prose`、shimmer |
| `src/components/ProgressBar.tsx` | 99 | 5 阶段进度条 + ETA（无状态） |
| `src/components/ReportView.tsx` | 88 | Markdown 分块延迟渲染 + 安全切片 |

### 测试（`web/frontend/e2e`，5 文件 / 444 行）

| 文件 | 用例数 | 覆盖 |
|---|---:|---|
| `research.spec.ts` | 7 | 主流程、刷新恢复、降级可见、导出、RAG 命中列表、取消 |
| `account.spec.ts` | 9 | 历史降级、账号条、上传拒绝、KB 列表/进度/队列、邀请、筛选/分页/预览、法律弹窗 |
| `error-handling.spec.ts` | 2 | 429 结构化错误 + 重试；未知 run_id 404 |
| `responsive.spec.ts` | 2 | 窄屏无横向溢出（桌面 + 移动两个 project） |
| `helpers.ts` | — | `settleRun` 清理（依赖状态徽标与「停止研究」文案） |

## 2. 页面板块地图（渲染顺序）

- **Header**：品牌块 → 连接状态 chip → AG-UI 徽标 → **AccountPanel**
- **左栏 aside（xl sticky + 独立滚动）**
  1. 新研究表单：主题、附加要求、档位 radio、提交/停止
  2. 运行边界（3 条说明）
  3. 文档摄取状态：命中计数 + RAG 命中文件列表
- **右栏**
  4. 状态卡：status-badge / run_id / 主题 H1 / 当前活动 + `ProgressBar`
  5. 错误卡（code/component/node/message/detail/hint + 同参重试）
  6. 超时卡
  7. 运行摘要 `run-summary`（6 项）
  8. 4 个指标卡（节点/Token/发现与来源/耗时）
  9. 研究活动时间线 + 降级与恢复
  10. 结果区：报告卡（复制/导出/`ReportView`）、引用卡、来源+反思、validator 统计
- **AccountPanel 子板块**：登录/注册门（portal）→ 配额 chip / 历史 / 上传 / 知识库 / 账号 → 历史面板（筛选/分页/预览）→ 知识库面板（上传队列 + 文档列表）→ 邀请注册弹窗 → 法律弹窗 → 历史报告预览弹窗

## 3. 状态与数据流

- **来源**：`useResearchStream` 打开 `/api/research/{id}/stream`，7 类事件按 `lastEventId` 去重后入 `events`；`RUN_FINISHED/ERROR` 收口。
- **派生**：`result` 只来自终局事件；实时计数来自最后 `STATE_DELTA`；`progress = computeProgress(summarize(events))`。
- **持久化（浏览器）**：仅 sessionStorage 两个键 —— `dr.lastRunId`（hook）与 `dr.lastRequest`（App）；无 localStorage、无状态管理库。
- **App.tsx 顶层**：10 `useState` + 1 `useRef` + 3 `useEffect` + 7 `useMemo`（详见 §5 热点）。
- **AccountPanel**：28 个 state/ref；`activeRunId`/`running` 仅作为刷新触发器（不渲染）。

## 4. HTTP 调用面

| 接口 | 调用处 | 触发 |
|---|---|---|
| `POST /api/research` | hook `start` | 提交 |
| `GET /api/research/{id}` | hook `resume` | 刷新恢复 |
| `POST /api/research/{id}/cancel` | hook `cancel` | 停止 |
| `GET /api/options` | App mount | 档位/鉴权开关 |
| `GET /api/research/{id}/report` | App `exportReport`、AccountPanel `openReport` | 导出/历史预览（两处重复实现） |
| `GET /api/auth/session` / `login` / `register` / `logout` | AccountPanel | 认证 |
| `DELETE /api/auth/account` | AccountPanel | 注销 |
| `GET /api/quota`、`GET /api/rag/docs` | AccountPanel `refreshSideData` | mount / run 边界 |
| `POST /api/rag/ingest`（XHR）、`GET /api/rag/ingestions/{id}` | AccountPanel | 上传 + 轮询 |
| `GET /api/runs` | AccountPanel | 历史面板 |
| `GET /api/legal/{doc}` | AccountPanel | 法律弹窗 |

## 5. 已知结构热点（重构对象）

1. `App.tsx`(952)：表单/看板/结果区 + ~25 个 helper 全在一个文件；
2. `AccountPanel.tsx`(773)：认证/历史/知识库三类领域 + 模态混杂；
3. 重复实现：`csrfHeaders`×2、错误解析×3（`readError`/`errorFromBody`/`toStructuredError`）、报告获取×2、状态词表×2、节点词表×2；
4. 类型内联：`Quota/RunBrief/RagDoc/UploadItem/LaunchParams` 散落组件，未收敛到 `types/`；
5. 无 `lib/api.ts`：每个调用点自建 headers 与错误解析；
6. 无 ESLint/Prettier；`tsconfig.include` 只含 `src`（e2e 不参与 `tsc`）；
7. 死代码/低效：`RunErrorEvent`、`ProgressInput`、`CHUNK_CHARS` 导出无消费方；`AccountPanel` 有一处两分支相同的冗余 effect；`events` 数组无上限；`tick` 纯强制重渲染。

## 6. 重构硬边界（行为契约，不得破坏）

- **`data-testid` 全套**（审计时 39 个，e2e 依赖；后续批次持续增补，R7 新增 `kb-delete`/`kb-delete-confirm`/`kb-delete-cancel`；清单见下述关键项）：`status-badge`、`error-card`/`error-code`/`error-detail`/`error-hint`/`retry-button`、`run-summary`、`report-heading`、`export-button`、`rag-hit-list`/`rag-hit-item`、`account-panel`、`history-toggle`、`rag-upload-input`、`kb-toggle`、`upload-state`、`history-panel`、`history-status-filter`、`history-error`、`flagged-badge`、`history-load-more`、`kb-panel`、`kb-refresh`、`upload-queue`、`upload-item`、`upload-progress`、`kb-error`、`kb-doc-item`、`invite-register`、`invite-close`、`invite-code-input`、`legal-modal`、`legal-close`、`history-preview`。
- **中文文案**：「研究进行中 / 研究完成 / 已取消 / 停止研究」等被 e2e 与 `settleRun` 依赖；
- **选择器**：`#topic`、`button[type="submit"]`、`article.report-prose`；
- **协议**：SSE 事件类型 / 字段、API 路径与响应结构、进度语义（不伪造摄取百分比）。

## 7. 重构目标目录（建议，供实施参考）

```
src/
  app/            # 壳与路由级布局（无路由则 AppShell）
  features/
    launch/       # 新研究表单、运行边界、文档摄取状态
    run/          # 状态/进度、错误、超时、摘要、指标、时间线、降级
    report/       # 报告卡、引用、来源、反思、validator 统计、ReportView
    account/      # 登录门、账号条、配额、法律弹窗
    history/      # 历史面板与预览
    knowledge-base/ # 上传队列、文档列表
  components/ui/  # ProgressBar 等通用件
  hooks/          # useResearchStream 等
  lib/            # api.ts（统一 fetch/CSRF/错误）、format.ts、storage.ts
  types/          # agui.ts + api.ts + domain.ts
```
