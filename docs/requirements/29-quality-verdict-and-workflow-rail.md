# 需求 29：质量结论与工作流定位（跑完 ≠ 通过 + 阶段栏）

> 状态：**草稿**（随 PR 合入后置「已合」）；实施进度以 `docs/project-status.md` 为唯一看板（D-01）。
> 来源：前端 UI 重构收口（验收点①/②）——`docs/web-frontend-audit.md` 与需求 28 实测暴露的两处呈现缺陷。

## 1. 元信息

| 项 | 值 |
|---|---|
| 编号 | 29（**占位**，待确认） |
| 标题 | 质量结论与工作流定位（跑完 ≠ 通过 + 阶段栏） |
| 优先级 | P1（呈现语义正确性） |
| 状态 | 草稿 |
| 负责人 | TianJinYing2006 |
| 关联 Issue | 待确认（用户需给口径：项目里需求号与 issue 号绑定） |
| 关联 PR | 待开（分支已建、未推送） |
| 创建 / 更新 | 2026-10-08 |
| 分支 | `feat/29-ui-quality-and-workflow`（**堆叠栈的第 1 层**，基于 `dev`） |

> 堆叠说明：本需求与 30/31/32 构成一组**堆叠分支**（每层基于前一层）。共享文件
> （`package.json` / `package-lock.json` / `.github/workflows/ci.yml` / `vite.config.ts`）
> **按层只带自己那一份增量** —— 例如本层只引入 vitest 与 ci 的 vitest 步骤，
> impeccable 与 ci 的守卫步骤归需求 32。

## 2. 问题背景

两处缺陷，都属「把状态呈现得比实际更确定」：

**（一）「跑完」被读成「通过」。** `lib/presentation.ts:statusPresentation()` 只有一维
运行状态——`done` 的标签是「研究完成」。但**跑完 ≠ 引用通过**：

- 需求 28 实测记录：「重型题写作后只剩 **1,517 token**，**112 条引用全部未校验**」；
- 这种运行同样会走到 `done`，状态徽标同样显示「研究完成」。

用户会把「没查过」读成「查过了且没问题」。这是本项目最忌讳的那类假信号
（与「不伪造进度」同源，见 `docs/web-frontend-redesign-directions.md` §2 方向 B 的诚实原则）。

**（二）页面没有流程定位。** 主列把新建表单、运行横带、报告、决策轨迹、活动流全堆在一起，
没有「整条链路走到哪、下一步干什么」的指示。

## 3. 需求分析

- **四态必须可区分且用词固定**：引用通过 / 确证失败 / 校验未完成 / 输出待审核；
- **两个维度正交**：任务维度（跑没跑完）与质量维度（查得怎么样）不可互相推导——
  取消的运行可以已有部分结论；跑完的运行可以一条都没校验；两者要能同时成立并分别呈现；
- **口径不由前端发明**：直接对齐后端权威定义（见 §5「设计策略」）；
- 阶段栏四段：新建研究 → 等待与进度 → 报告阅读 → 引用核查，
  **刻意不做五段**——用户口径里的「导出/分享」在本页没有独立区块，它是报告卡片头部的一排按钮、
  与「报告阅读」同处一个锚点；为它单列会得到一个指向同一锚点的重复项。阶段栏宁可少一段，
  也不要造一个假的落点。「导出/分享」的可达性由「报告阶段已完成」表达（报告未产出时导出按钮本就禁用）；
- 阶段栏的 `current` 与 `done` **互斥**；未到达的阶段**不渲染成链接**。

## 4. 当前设计（代码位置）

| 位置 | 现状 |
|---|---|
| `web/frontend/src/lib/presentation.ts` | `statusPresentation(status)` 一维运行状态；无质量维度 |
| `web/frontend/src/features/run/RunPanels.tsx` | `RunStrip` 只渲染 `status-badge`；`RunSummary` 直接列 6 项数字，先谈耗时/节点/字数 |
| `web/frontend/src/features/report/ReportPanels.tsx` | `EvidenceMargin` 已算 `verification_failed` 计数，但只在页边栏文案里出现 |
| `web/frontend/src/types/agui.ts` | `CitationResult` 已有 `verified` / `verified_relaxed` / `verification_failed` / `existence` |
| `web/frontend/src/App.tsx` | 单列平铺，无区块锚点、无阶段栏 |
| `web/frontend/src/features/run/RunPanels.tsx` `ErrorCard` | **未读** `error.retryable`——只要有 `lastRequest` 就渲染「用同样参数重试」 |

## 5. 优化方案

**（1）质量判定模型 `web/frontend/src/lib/quality.ts`（新增，221 行）**

- `citationVerdict(citation)` → 四态，顺序即语义：
  `verification_failed` **先判** → `unknown`；`verified` → `verified`；
  `verified_relaxed` → `relaxed`；否则 `refuted`。
  （先判 `verified` 会把「没查清」读成「查过且通过」——后端可能出现两者同时为真的组合。）
- `qualityVerdict(result, finished)` → 七个 `kind`：
  `under-review` / `refuted` / `incomplete` / `relaxed` / `passed` / `no-citations` / `pending`；
- **严重度优先级**：待审核 > 确证失败 > 校验未完成 > 宽松 > 通过。
  「确证失败」排在「校验未完成」之前，因为前者是**已查实的坏消息**、后者是**未知**；
  但两者计数都完整保留在 `counts` 里，界面可同时呈现，不做信息丢失；
- `isTaskComplete(finished)` → 任务维度：`!cancelled && stop_reason === 'completed'`。

**（2）阶段模型 `web/frontend/src/lib/workflow.ts`（新增，98 行）**

- `workflowStages({ status, hasResult, qualityKind })` → 四段 + `interrupted`；
- 四段锚点：`stage-compose` / `stage-run` / `stage-report` / `stage-verify`；
- `current` 取**第一个未完成**阶段；全部完成为 `null`；中断且无报告时也是 `null`（不回落到「报告阅读」）。

**（3）呈现层**

- `presentation.ts` 增 `qualityTonePresentation(tone)`（与既有的状态/连接映射同住，避免组件里散落色值）；
- `RunStrip` 改为两个**并排但不合并**的徽标：`status-badge`（跑没跑完）+ `quality-badge`（查得怎么样）；
- `RunSummary` 顶部新增 `quality-summary`：**先回答质量**，再列耗时/节点/字数；
- 新增 `features/workflow/WorkflowRail.tsx`（84 行）：阶段栏，`aria-current="step"`，
  只有「已到达或当前」的阶段渲染成 `<a href="#锚点">`；
- `App.tsx` 挂载阶段栏；`LaunchForm`（折叠/展开两个根元素）、`RunStrip`、`ReportCard`、证据边栏
  分别加 `id="stage-*"`。

**（4）`retryable` 修复**

`ErrorCard` 判据改为 `error.retryable !== false`（而非 `=== true`）：
老后端不下发该字段时保持原行为（显示按钮），只有**显式**为 false 才隐藏。
反例即动机：配额熔断（`quota_exceeded`）重试必然再失败，
给它一个「用同样参数重试」的按钮是在骗用户点第二次。

**（5）本层顺带引入的工具链**（vitest 唯一的使用方在本需求）

- `vitest` 入 devDependencies + `test:unit` 脚本；
- `vite.config.ts` 的 `test.include` **必须**显式限定到 `src/**/*.test.ts`——
  vitest 默认 include 是 `**/*.{test,spec}.?(c|m)[jt]s?(x)`，会把 Playwright 的
  `e2e/*.spec.ts` 一并吞进来，实测报 9 个「Playwright Test did not expect test.describe()」；
- ci.yml 增 `Unit tests (vitest)` 步骤。

## 6. 设计策略

- **口径直读后端，不在前端发明**（写进 `quality.ts` 文档串）：
  - 后端 `repair.py`：**只有「确证失败」可删**（`verified=False` 且 `verification_failed=False`）；
    UNKNOWN（`verification_failed=True`）**必须保留** ⇒ 两者不同质；
  - 后端 `render.py`（可信声明）：三口径 —— 存在性 / 忠实度（严格，`verified` = 存在且忠实）/
    宽松口径（`verified_relaxed` = 存在且（忠实或多源印证））；
- **蕴含而非依赖调用方保证顺序**：`hasResult` 蕴含「新建」「运行」两步已完成
  （`done.compose = started || hasResult`）。这样模型对任意输入自洽，
  不会出现「报告已完成、但新建研究未完成」的阶段倒挂；
- 阶段栏的视觉约束对齐方向 B 与既有 skill 规则：**单一强调色**（只有当前阶段用 `stamp-blue`）、
  不用编号标记（设计文档 §1 把「01/02/03」列为模板化 tell，改用对勾表达「走过」）、
  无渐变无光晕、窄屏 `flex-wrap` 不产生横向滚动。

## 7. 验收标准（DoD）

- [x] `lib/quality.ts` 四态判定 + 七 kind，严重度优先级与 `counts` 完整保留
- [x] `lib/quality.test.ts` 16 条单测通过（含需求 28 真实形态：112 条全未校验 ⇒ `incomplete` 且 `taskComplete === true`）
- [x] `lib/workflow.ts` 四段模型 + `lib/workflow.test.ts` 9 条单测通过（含「恰好/至多一个当前阶段」「阶段单调推进」两条不变式）
- [x] `RunStrip` 双徽标（`status-badge` + `quality-badge`），二者可同时成立且互不覆盖
- [x] `RunSummary` 先呈现 `quality-summary`，再列数字
- [x] `WorkflowRail` 阶段栏 + 四个真实锚点；未到达阶段不渲染成链接
- [x] `ErrorCard` 尊重 `retryable`（`!== false` 判据）
- [x] `tsc --noEmit` = 0；`vite build` = 0；`vitest run` = **25 passed**（16 + 9）
- [x] E2E 全量 **63 passed**（栈顶分支、对**全新构建的 dist**；含本需求新增 2 条阶段栏用例）
- [x] 四个既有守卫 + UI 反模式守卫 exit 0；`ruff check .` All checks passed
- [ ] CI 全量（`lint-and-test` + `infra` + `frontend` + `e2e`）**真跑**零回归 —— 未推送，CI 未触发
- [ ] 推送分支并开 PR，回填 PR 号
- [ ] 全量 pytest 套件重跑（本轮只跑了守卫单测 `tests/test_ui_pattern_guard.py` = 17 passed）
- [ ] 正式需求编号与关联 Issue 确认（现用 29 占位）

## 8. 影响范围与风险

**模块**：`lib/quality.ts`（新）、`lib/quality.test.ts`（新）、`lib/workflow.ts`（新）、
`lib/workflow.test.ts`（新）、`lib/presentation.ts`、`features/run/RunPanels.tsx`、
`features/report/ReportPanels.tsx`、`features/launch/LaunchPanels.tsx`、
`features/workflow/WorkflowRail.tsx`（新）、`App.tsx`、`e2e/research.spec.ts`、
`vite.config.ts`、`package.json`、`package-lock.json`、`.github/workflows/ci.yml`。

**风险与已发生的实测教训**：

- **阶段栏 `current` 与 `done` 曾经同时成立**：初版写成「全部完成时 `current` 停在末段」，
  组件按 `current` 优先渲染 ⇒ 把一个**已完成**的阶段高亮成「你在这」，读起来像流程还没走完。
  E2E 实测抓到（期望 `done`、实得 `current`）。已改为**互斥**语义，
  并在单测里加「当前阶段必未完成」的不变式锁死；
- **未到达阶段的锚点是坏门面**：`#stage-report` 由 `{result && <ReportCard/>}` 决定存在与否，
  若把未到达阶段也渲染成链接，点了没反应（比不可点更糟）。已改为非链接元素，
  并加 E2E 用例遍历阶段栏所有链接、逐个断言锚点真实存在；
- **`retryable` 判据方向**：用 `=== true` 会在老后端不下发该字段时把重试入口整体隐藏，
  属功能回归；故用 `!== false`；
- 兼容性：`quality.ts` / `workflow.ts` 均为纯函数，无副作用、无外部依赖，可单测；
  阶段栏在窄屏 `flex-wrap`，不引入横向溢出（由既有 `responsive.spec` 覆盖）。

## 9. 测试策略

- **单测（vitest，新增基础设施）**：
  - `src/lib/quality.test.ts` 16 条：四态判定与优先级、任务/质量两维度正交、
    严重度优先级、`counts` 完整性、需求 28 真实形态回归；
  - `src/lib/workflow.test.ts` 9 条：阶段推进、中断态、**至多一个当前阶段**、
    **当前阶段必未完成**、阶段单调推进、锚点稳定性；
- **E2E（Playwright）**：`e2e/research.spec.ts` 新增 2 条 ——
  阶段栏随流程从新建推进到报告与引用核查（判据用 `data-stage-state`，不断言颜色类名）；
  阶段栏渲染出的每个链接都指向真实存在的区块；
- **契约保护**：既有 `data-testid` 与中文文案契约全部保留（E2E 全量 63 passed 即为证据）。
  本需求新增 `workflow-rail` / `workflow-stage-{compose,run,report,verify}` /
  `workflow-interrupted` / `quality-badge` / `quality-summary` 等新 testid。

## 10. 变更记录

| 日期 | 类型 | 原因 | 改动摘要 | 关联 PR/commit |
|---|---|---|---|---|
| 2026-10-08 | 新增 | 需求 28 实测「112 条引用全未校验却走到 done」暴露呈现缺陷；页面缺流程定位 | 质量判定模型 + 阶段栏 + `retryable` 修复 + vitest 基础设施 | 待开（本地 commit `addfd37`，分支 `feat/29-ui-quality-and-workflow`） |
