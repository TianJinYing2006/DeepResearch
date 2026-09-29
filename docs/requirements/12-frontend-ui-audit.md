# 需求 12：前端 UI 合规审计（web-design-guidelines 全量审查）

> 状态：**已合**（审计批次 PR #60；R1~R7 改造批次全部收口，R7 已回填本 DoD）。本文件是审计需求载体；实施进度以 `docs/project-status.md` 为唯一看板（D-01）。
> 前置事实（2026-09-29 实测）：前端 `src/` 共 9 个文件 / 2,707 行；pytest 455 全绿、Playwright E2E 10/10；
> 但**无任何 a11y / 设计规范审计手段**（无 ESLint、无 Prettier、无 axe、无设计系统文档）。
> 需求 11 已暴露「静默失效」这类缺陷——根子在**没有审计机制**，缺陷只能靠偶然发现。
> 飞书镜像：待同步（本文件为审计类需求，命名按本地 `12-frontend-ui-audit`）。

## 1. 元信息

| 项 | 值 |
|---|---|
| 编号 | 12 |
| 标题 | 前端 UI 合规审计（web-design-guidelines 全量审查） |
| 优先级 | P2 |
| 状态 | 已合 |
| 负责人 | TianJinYing2006 |
| 关联 Issue | [#57](https://github.com/TianJinYing2006/DeepResearch/issues/57)（GitHub；PR 与 Issue 共用编号空间，故 Issue 号与需求编号不一致，映射以本行为准） |
| 关联 PR | [#60](https://github.com/TianJinYing2006/DeepResearch/pull/60)（审计报告落库）；R1~R7 改造批次见 `docs/web-frontend-audit.md` §8 |
| 创建 / 更新 | 2026-09-29 |

## 2. 问题背景

### 2.1 现状一句话

> **功能有一套 E2E 兜底，视觉与可访问性零兜底。**

### 2.2 结构性风险（2026-09-29 盘点实测）

| 项 | 实测 |
|---|---|
| `src/App.tsx` | 952 行：10 useState + 1 useRef + 3 useEffect + 7 useMemo + ~25 个模块级 helper/component 挤在一个文件 |
| `src/components/AccountPanel.tsx` | 773 行：28 个 state/ref、11 个 API 调用、4 个 portal 弹窗 + 2 个内联面板 |
| 审计手段 | 无 ESLint / 无 Prettier / 无 axe / 无设计令牌文档 |
| E2E 覆盖 | 20 条；10 个 testid 无任何用例覆盖（含整个 auth-gate 流程与 timeout UI） |

### 2.3 为什么先审计而不是直接改造

需求 11 已经证明：**缺陷可以是「构建绿、E2E 绿、肉眼几乎看不出」的**。
若不做审计直接凭直觉改视觉，既无法证明改对了，也无法证明没改坏。
按项目「诚实基线 + 数据驱动」口径，先拿证据再动手。

## 3. 需求分析

### 3.1 目标

在**不改一行源码**的前提下，产出一份可核对、带 `file:line` 的 UI 问题清单，
作为后续视觉改造的**输入证据**（实际以 R1~R7 批次落地，见 `docs/web-frontend-audit.md` §8；未另立需求 13）。

### 3.2 量化成功定义

- `src/` 全部 9 个文件 100% 审过；
- 每条问题含 `file:line` + 严重度 + 建议动作；
- 本需求源码 diff 为 0。

## 4. 当前设计

### 4.1 现有验证覆盖了什么

- 后端：pytest 455（研究引擎 + API 契约）
- 前端 E2E：20 条，断言的是**功能**——testid、中文文案、`scrollWidth <= clientWidth`

### 4.2 明确不覆盖

对比度（WCAG）、键盘可达性、focus 可见性、语义化标签、aria 属性、
reduced-motion、触摸目标尺寸、表单标签关联、错误提示可感知性。

## 5. 优化方案

### 5.1 选型的 skill

`vercel-labs/agent-skills` → `skills/web-design-guidelines`（实测 **31,663★**，2026-08-28 更新）

- 工作机制：fetch 远端规则 → 逐个读文件 → 输出 `file:line` 结论
- **只读**、不改代码，天然契合「先诊断后动手」

### 5.2 审计范围（9 个文件 / 2,707 行）

| 文件 | 行数 | 预期产出 |
|---|---|---|
| `src/main.tsx` | 10 | 极少 |
| `src/App.tsx` | 952 | **主要** |
| `src/index.css` | 203 | 中（prose 系统、reduced-motion） |
| `src/components/AccountPanel.tsx` | 773 | **主要**（4 个弹窗的 focus/aria） |
| `src/components/ProgressBar.tsx` | 99 | 中（progressbar 语义） |
| `src/components/ReportView.tsx` | 88 | 中（Markdown 表格滚动、标题层级） |
| `src/hooks/useResearchStream.ts` | 294 | 少（非 UI 渲染） |
| `src/lib/progress.ts` | 126 | 极少（纯函数） |
| `src/types/agui.ts` | 162 | 极少（纯类型） |

### 5.3 产出物

`docs/web-frontend-audit.md`（实际文件名）：按 **P0 / P1 / P2** 分级，每条含 `file:line` + 问题描述 + 建议动作。

## 6. 设计策略

- **只装 `web-design-guidelines`，不装 impeccable**（2026-09-29 主理人拍板）：
  前者纯 md 指令包、无脚本、只读、结论可核对；后者 72,027★ 能力更强
  （`audit` / `extract` / `document`），但仓库含 300 个 `.rs` + 230 个脚本、
  launcher 首次运行**自下载二进制** ⇒ 供应链风险需单独过安全审计后再议。
- **本次不改代码**：需求阶段纪律 + 避免与需求 11 分支冲突；改造另开需求 13。
- **两条已知会撞车的规则，审计结论照单全收会出事**（实施时按此裁断）：
  1. 「Dashboard 别用左侧栏」—— 本项目是 `xl:grid-cols-[360px_1fr]` 工作台侧栏，**不动**；
  2. 「换掉 Inter」—— 属视觉改版决策，留给需求 13，不在本审计中落地。

## 7. 验收标准（DoD）

- [x] skill 安装前已人工审读 `SKILL.md` 及附带文件（复核记录见 `docs/web-frontend-redesign-directions.md` §R6b-1.5）
- [x] 9 个源文件全部审过，产出 `docs/web-frontend-audit.md`（79 项发现）
- [x] 每条问题含 `file:line` + 严重度（P0/P1/P2）+ 建议动作
- [x] 源码 diff 为 0（审计批次 PR #60 仅新增 docs；R1~R7 改造为独立批次，各自独立验证）
- [x] CI 仍绿：`frontend` job（tsc + build）与 `e2e` job（审计时 10/10；R7 收口后全量 35 条）

## 8. 影响范围与风险

| 项 | 说明 |
|---|---|
| 动到文件 | 仅新增 `docs/web-frontend-audit.md`；skill 落在用户级目录，不进仓库 |
| 主要风险 | skill 需 fetch 远端规则 ⇒ 依赖网络。断网则降级为人工按已知清单审，并在报告中标注降级 |
| 次级风险 | 审计结论可能与现有 E2E 断言或既定布局冲突 ⇒ 由 §6 两条裁断规则兜底 |
| 供应链 | 该 skill 无脚本、纯指令包，风险面小；仍执行安装前审读 |

## 9. 测试策略

- 本需求无代码改动 ⇒ 无新增测试
- 校验方式：`git status` 显示仅新增 `docs/` 下文件；CI `frontend` + `e2e` job 全绿

## 10. 变更记录

| 日期 | 类型 | 原因 | 改动摘要 | 关联 PR/commit |
|---|---|---|---|---|
| 2026-09-29 | 优化 | 需求立项 | 建立前端 UI 审计基线（只出报告，不改代码） | — |
| 2026-09-29 | 收口 | R7 回填 DoD | 状态转「已合」；DoD 5 项勾选；文件名引用统一为 `docs/web-frontend-audit.md`；明确视觉改造以 R1~R7 批次落地、未另立需求 13 | R7（本批次） |
