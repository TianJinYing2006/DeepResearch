# 需求 31：移动端与异常态回归（含 E2E 假绿缺陷修复）

> 状态：**草稿**（随 PR 合入后置「已合」）；实施进度以 `docs/project-status.md` 为唯一看板（D-01）。
> 来源：前端 UI 重构收口（验收点④）——移动端仅跑布局用例、异常态零覆盖，且发现 E2E 会产出假绿。

## 1. 元信息

| 项 | 值 |
|---|---|
| 编号 | 31（**占位**，待确认） |
| 标题 | 移动端与异常态回归（含 E2E 假绿缺陷修复） |
| 优先级 | P1（测试防线缺陷：假绿比没测试更危险） |
| 状态 | 草稿 |
| 负责人 | TianJinYing2006 |
| 关联 Issue | 待确认（用户需给口径：项目里需求号与 issue 号绑定） |
| 关联 PR | 待开（分支已建、未推送） |
| 创建 / 更新 | 2026-10-08 |
| 分支 | `test/31-mobile-and-fault-regression`（**堆叠栈的第 3 层**，基于 `chore/30-account-panel-domain-state`） |

> 堆叠说明：本层带的是自己的增量 —— `playwright.config.ts`（project 分流）、
> `package.json`（`e2e` / `e2e:only` 脚本）、`ci.yml`（e2e job 直调 playwright）；
> `package-lock.json` 本层不动（依赖未变）。

## 2. 问题背景

两件事：

**（一）移动端的覆盖面名不副实。** `playwright.config.ts` 的 `mobile` project 用
`testMatch: /responsive\.spec\.ts/` 只跑 **2 条布局用例**（判据是「无横向滚动」）。
**布局不溢出 ≠ 流程能用**：窄屏下按钮可能换行到视野之外、弹窗可能高过视口、
面板可能被遮挡——这些 `scrollWidth <= clientWidth` 一条都查不出来。
登录、上传、历史预览、长报告四条主流程在窄屏**完全没有验证**。

**（二）三条异常态在任何视口都没有覆盖。**
`research.spec.ts` 只覆盖「停止成功」路径，不覆盖「停止请求在途」这个中间态；
`error-handling.spec.ts` 只有并发闸 429，没有配额耗尽的 `quota_exceeded`；
断线重连则完全没有用例。

**（三）E2E 会产出假绿（本轮实测踩到）。** Playwright 的 `webServer` 由 FastAPI 托管
`web/frontend/dist/` —— 也就是说 **E2E 跑的是构建产物，不是 `src`**。
改完源码不重新 `build` 就跑用例，会看到「全绿」但测的是旧包。
本轮真实踩到：前三轮改完 `AccountPanel` 后直接跑 e2e，那几次「51 passed」
**并未验证当时的改动**。

## 3. 需求分析

- 移动端补齐四条主流程的窄屏用例，且判据要能查出「元素被挤出视野」而不只是「页面被撑宽」；
- 三条异常态补齐，且**两个视口都跑**（它们与视口正交，判据是状态徽标与错误码，不是布局）；
- 修掉 E2E 假绿：让本地跑用例**不可能**越过「必须先构建」这一步；
- 断言要防「只证明 UI 改了字」——例如断线恢复不能只断言徽标文案变化，
  必须断言**报告真的产出**。

## 4. 当前设计（代码位置）

| 位置 | 现状 |
|---|---|
| `web/frontend/playwright.config.ts` | `mobile` project `testMatch: /responsive\.spec\.ts/`（仅 2 条布局用例）；`desktop` project `testMatch: /.*\.spec\.ts/` |
| `web/frontend/playwright.config.ts` `webServer` | `command: uvicorn web.backend.main:app ...` 由 FastAPI 托管 `dist/`（**构建产物**） |
| `web/frontend/package.json` | `"e2e": "playwright test"` —— **不构建** |
| `web/frontend/e2e/` | 9 个 spec：account / closure / error-handling / help-feedback / history-management / password-reset / research / responsive / share |
| `.github/workflows/ci.yml` `e2e` job | 先 `npm run build`，再 `npm run e2e` |

## 5. 优化方案

**（1）新增 `e2e/faults.spec.ts`（98 行，3 条 × 2 视口）**

| 用例 | 构造方式 | 判据 |
|---|---|---|
| 实时流断开时显示「正在重连」，恢复后仍能跑到报告 | 只 `route.abort()` **第一次** `/api/research/*/stream`（EventSource 自动重连，第二次放行走真实后端） | 页头出现「正在重连」→ **报告真的产出** → 已脱离重连态 |
| 取消请求在途时状态呈现「正在安全停止」 | 把 `POST /api/research/{id}/cancel` 拖住 3s，制造「已请求取消、后端尚未确认」的窗口 | 状态徽标是「正在安全停止」而**不是**「已取消」 |
| 配额耗尽时展示结构化错误码 | `POST /api/research` → 429 `quota_exceeded`（`retryable: false`） | `error-code` = `quota_exceeded` + `error-hint` 含「月度预算」+ **不提供**重试入口 |

**（2）新增 `e2e/mobile-flows.spec.ts`（161 行，4 条，只跑移动端）**

登录 / 上传入口与队列 / 历史面板与报告预览 / 长报告 + 引用核查 + 导出入口。

判据用新写的 `expectWithinViewport()`：元素的 `boundingBox` 左右边缘都必须落在视口宽度内。
与 `responsive.spec.ts` 的 `scrollWidth <= clientWidth` **互补**——
后者查「页面被撑宽」，前者查「元素被挤出视野」（`overflow-hidden` 裁剪、或定位到视口之外）。

**（3）`playwright.config.ts` 的 project 分流**

```
desktop: testMatch /.*\.spec\.ts/   +  testIgnore /mobile-flows\.spec\.ts/
mobile:  testMatch /(responsive|mobile-flows|faults)\.spec\.ts/
```

- `responsive` / `faults`：判据与视口正交 ⇒ **两个视口都跑**；
- `mobile-flows`：断言前提就是窄屏本身 ⇒ **只跑移动端**；
- 用 `testMatch` / `testIgnore` 分流，**不用** `beforeEach` 里的 `test.skip()`
  （配置里已有告诫：skip 掉的用例仍会执行 `afterEach`，页面可能尚未导航 ⇒ 清理钩子挂死）。

**（4）修掉 E2E 假绿**

| 改动 | 内容 |
|---|---|
| `package.json` | `"e2e": "npm run build && playwright test"` —— 让脚本本身保证「测的是当前源码」 |
| `package.json` | 新增 `"e2e:only": "playwright test"` —— 显式命名，供对当前 `dist` 快速迭代，不伪装成完整验证 |
| `playwright.config.ts` | 在 `webServer` 处写明「这个 server 托管 `dist/`，不 build 会假绿」 |
| `ci.yml` | e2e job 的 `run` 由 `npm run e2e` 改为 `npx playwright test`（该 job 上一步已 build，避免重复构建） |

## 6. 设计策略

- **异常态与视口正交的放两个视口，与视口耦合的只放移动端**——避免无谓地把每条用例跑两遍；
- **断言要能失败**：断线恢复的判据是「报告产出」而非「徽标变了」；
  取消中的判据是「不是终态」而非「出现了某段文字」；
- **全部用路由拦截或 `DR_DEMO` 假数据构造**，零 LLM、不占后端并发闸；
- 所有用例在 `afterEach` 走 `settleRun()`（后端并发闸为 1，未终局的运行会让失败**传染**）。

## 7. 验收标准（DoD）

- [x] `e2e/faults.spec.ts` 三条异常态落地，两个视口都跑（desktop + mobile 各 3 条）
- [x] `e2e/mobile-flows.spec.ts` 四条窄屏流程落地，只跑移动端
- [x] `playwright.config.ts` project 分流更新（`testIgnore` 排除 desktop 上的 mobile-flows）
- [x] `e2e` 脚本改为「先 build 再跑」，新增 `e2e:only`；`webServer` 处写明假绿风险
- [x] `ci.yml` e2e job 改直调 `npx playwright test`，避免重复构建
- [x] E2E 用例总数 **51 → 63**；栈顶全量 **63 passed**（对**全新构建的 dist**，2.2 分钟）
- [x] `tsc --noEmit` = 0；`vite build` = 0；`vitest run` = 25 passed（本层未改单测）
- [x] 四个既有守卫 + UI 反模式守卫 exit 0；`ruff check .` All checks passed
- [ ] CI 全量**真跑**零回归 —— 未推送，CI 未触发
- [ ] 推送分支并开 PR，回填 PR 号
- [ ] 正式需求编号与关联 Issue 确认（现用 31 占位）

## 8. 影响范围与风险

**模块**：`web/frontend/e2e/faults.spec.ts`（新）、`web/frontend/e2e/mobile-flows.spec.ts`（新）、
`web/frontend/playwright.config.ts`、`web/frontend/package.json`、`.github/workflows/ci.yml`。

**风险与已发生的实测教训**：

- **E2E 假绿（最严重，已修）**：`webServer` 托管 `dist/`，不重建即假绿。
  本轮因此让**前三轮的 E2E 结论全部失效**，必须重建后重跑才算数。
  已将「必须 build」编进 `npm run e2e` 脚本本身，而不是只靠文档提醒——
  注释挡不住人，脚本能；
- **断线恢复的终态文案不是「实时连接」**：运行结束后 EventSource 正常关闭 ⇒ 页头是
  「连接已关闭」。初版用例断言 `实时连接` 而失败；已改为断言
  「已脱离重连态」（`not.toContainText('正在重连')`）并注释说明原因；
- **`share-button` 不必然存在**：它受后端 `options.share_enabled` 开关控制，`DR_DEMO` 下未开启。
  长报告用例已改为断言「导出」与「复制正文」两个必然存在的入口；
- **配额耗尽用例依赖需求 29 的 `retryable` 修复**：该用例断言「不提供重试入口」，
  若 `ErrorCard` 未尊重 `retryable` 则必然失败。这两条需求在此处**有依赖**，
  合并顺序上 29 必须先于 31；
- 移动端用例的稳定性：窄屏视口下弹窗高度、按钮换行都可能随内容变化，
  判据刻意用 `boundingBox` 而非像素快照，避免脆性。

## 9. 测试策略

- **E2E 是唯一手段**（本层不引入单测）：目标本身就是「浏览器里的真实流程与异常态」；
- 运行口径：`npm run e2e`（先构建）+ `DR_PYTHON` 指向项目 venv + `E2E_CHANNEL=chrome`；
- 快速迭代：`npm run e2e:only`（不构建，明确只对当前 `dist`）；
- 隔离性：全部用例走路由拦截或 `DR_DEMO` 假数据，零 LLM 成本、不依赖真实任务库；
- 清理：`afterEach` 统一 `settleRun()`，避免未终局运行污染后续用例（并发闸为 1）。

## 10. 变更记录

| 日期 | 类型 | 原因 | 改动摘要 | 关联 PR/commit |
|---|---|---|---|---|
| 2026-10-08 | 新增 + 缺陷修复 | 移动端仅跑布局用例、异常态零覆盖；且实测发现 E2E 假绿 | 新增 faults / mobile-flows 两个 spec（51 → 63）；`e2e` 脚本改先 build；CI e2e job 改直调 | 待开（本地 commit `e01e712`，分支 `test/31-mobile-and-fault-regression`） |
