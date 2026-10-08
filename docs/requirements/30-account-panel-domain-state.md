# 需求 30：AccountPanel 领域状态拆分（含审计 U41 / U25 修复）

> 状态：**草稿**（随 PR 合入后置「已合」）；实施进度以 `docs/project-status.md` 为唯一看板（D-01）。
> 来源：前端 UI 重构收口（验收点③）——`AccountPanel.tsx` 长期集中管理账号、配额、知识库与多个弹窗。

## 1. 元信息

| 项 | 值 |
|---|---|
| 编号 | 30（**占位**，待确认） |
| 标题 | AccountPanel 领域状态拆分（含审计 U41 / U25 修复） |
| 优先级 | P2（可维护性；其中 U41 为数据正确性问题，按 P1 对待） |
| 状态 | 草稿 |
| 负责人 | TianJinYing2006 |
| 关联 Issue | 待确认（用户需给口径：项目里需求号与 issue 号绑定） |
| 关联 PR | 待开（分支已建、未推送） |
| 创建 / 更新 | 2026-10-08 |
| 分支 | `chore/30-account-panel-domain-state`（**堆叠栈的第 2 层**，基于 `feat/29-ui-quality-and-workflow`） |

> 堆叠说明：本层**不动** `package.json` / `package-lock.json` / `ci.yml` /
> `playwright.config.ts`——它们分别由第 1 层（29）与第 3/4 层（31/32）按增量携带。
> 本层只碰 `src/` 下的组件与 hook，与相邻层**零文件重叠**。

## 2. 问题背景

`AccountPanel.tsx` 是前端最大的单文件，且**内部没有定义任何函数组件**——
全部 JSX 在一个 `return` 里内联展开，钩子调用点合计 37 个
（29 `useState` + 1 `useRef` + 3 `useCallback` + 3 `useEffect` + 1 个 `useUploads`）。

一个文件同时持有：账号会话、配额、知识库文档清单与容量、上传状态机、历史面板开关、
法律文本弹窗、分块预览弹窗、邀请注册、帮助/反馈/分享管理弹窗、跨域错误条。
任何一处改动都要在数百行里定位，且**领域边界只存在于读者脑中**。

同时暴露两个**已登记但未修完**的缺陷（见 §8「登记与实现的漂移」）。

## 3. 需求分析

- 拆出**领域状态**（用户口径明确是「领域状态」，重点在 state 而非 JSX）；
- **零行为改动**：不破坏任何 `data-testid`、中文文案、DOM 顺序与焦点语义；
- 拆分手法必须让 `diff` 尽可能小——因此采用「hook 解构时**沿用原名**（含 setter）」，
  使 JSX 与全部调用点**一行不改**；
- 顺带修完 U41 与 U25。

## 4. 当前设计（代码位置）

拆分前（`dev` 版，**714 行**）的领域分布：

| 领域 | 行号区间 | 内容 |
|---|---|---|
| 模块级纯函数 | 18–36 | `locatorLabel()`、`inviteFromLocation()` |
| 状态声明区 | 53–95 | 29 个 `useState` + 1 个 `useRef` |
| `loadSession` | 97–111 | `GET /api/auth/session` |
| `refreshSideData` | 113–132 | **一次并发打 3 个接口**：quota + rag/docs + rag/usage |
| `useUploads` | 134–135 | 上传状态机（已抽到 `features/knowledge-base/useUploads.ts`） |
| `resetLocalData` | 137–148 | 登出/注销的**跨域清理编排** |
| `openLegal` | 182–196 | 法律文本 |
| `deleteDoc` / `kbMutate` | 198–241 | 知识库写操作 |
| `openPreview` | 243–261 | 分块预览 |
| `legalModal` 常量 | 299–323 | 法律弹窗 JSX（在 **3 处**分支里复用） |
| 账号条 / KB 面板 / 预览弹窗 / 邀请弹窗 | 358–711 | 主体 JSX |

## 5. 优化方案

四个切片，全部为**纯搬移**：

| 切片 | 新文件 | 行数 | 内容 |
|---|---|---|---|
| S7 法律文本域 | `features/account/LegalModal.tsx` | 98 | `useLegalDoc()` + `LegalModal`；三处重复渲染合并 |
| S6 分块预览 | `features/knowledge-base/ChunkPreviewModal.tsx` | 135 | `useChunkPreview()` + `ChunkPreviewModal` + `locatorLabel` |
| S4+S5 state | `hooks/useSideData.ts` | 81 | `quota` / `docs` / `docsError` / `kbUsage` + 合并刷新 |
| S5 actions | `features/knowledge-base/useRagDocActions.ts` | 110 | 删除 / 重命名 / 重分块 / 重嵌入 + 6 个操作态 |

**改造成果**：`AccountPanel.tsx` **714 → 555 行**（首轮四切片 714 → 572，后续 S10 / S2 / S1 三刀 572 → 555）。手法为「解构沿用原名（含 setter 与 `loadSession`）⇒ 调用方零改动」，已在全部 7 个切片上验证。

**顺手修完的两个审计项**：

- **U41 跨账号状态泄漏**：`resetLocalData` 的注释写着「登出/注销后清空本人可见的本地状态，
  避免上一账号数据闪现」，但它只清了配额与文档清单，**漏了知识库一侧**——
  `kbUsage`（面板里的「已用 X MB」）与 `previewDoc`（分块预览弹窗，**连正文一起**）
  都没清。登出后换个账号登录，上一账号的用量与预览正文会再次显示，
  与该函数自己的注释正好相反。已按切片逐项补全；
- **U25 未捕获的 promise 拒绝**：`refreshSideData` 用裸 `await Promise.all([...])` 且无 `try/catch`——
  三个接口里**任一**网络失败就让整个刷新以未捕获拒绝收场：另外两个已经拿到的好数据一起丢，
  且调用点全是 `void refreshSideData()`，没人接这个拒绝。已改为 `allSettled` 逐资源结算
  （成功几个更新几个，失败的那个单独置错误态）。

## 6. 设计策略

- **沿用原名的解构**：`const { quota, docs, docsError, kbUsage, refresh: refreshSideData, clear: clearSideData } = useSideData()`
  —— 局部名不变，于是 JSX 与全部 **9 个** `refreshSideData` 调用点零改动。`useRagDocActions`
  连 setter 一起返回（JSX 里既有读取也有直接写入：进入/取消两步删除、开始重命名、
  输入框 onChange、Escape 退出），若只返回动作函数就必须逐个改写调用点——diff 大、风险高而收益为零；
- **跨域依赖显式化**：`useRagDocActions({ refresh, onError })` —— `refresh` 来自 `useSideData`；
  `onError` 注入 `setBarMessage`，因为**删除失败走的是账号条的错误通道**（不是知识库自己的
  `kbActionError`）。这个不对称是既有行为，此处用回调参数把它摆到明面上，而不是让它继续藏在闭包里；
- **唯一的跨域写操作集中登记**：`resetLocalData` 是切片间唯一的共同写操作，
  每个持有账号级状态的切片都必须在此登记一行（已写进函数注释）。

## 7. 验收标准（DoD）

- [x] S7 / S6 / S4+S5 state / S5 actions / S10 / S2 / S1 七个切片落地，`AccountPanel.tsx` 714 → 555 行（仅剩 S8 认证入口 JSX）
- [x] **零行为改动**：所有 `data-testid`、中文文案、DOM 顺序与焦点语义保持不变
- [x] U41 修复：登出/注销后 `kbUsage`、`previewDoc`/`previewChunks`/`previewTotal`/`previewError`、
      `deleteDocId`/`deletingDoc`/`renamingDocId`/`renameValue`/`kbBusyDocId`/`kbActionError` 全部归零
- [x] U25 修复：`refreshSideData` 改 `allSettled` 逐资源结算，不再抛出未捕获拒绝
- [x] `tsc --noEmit` = 0；`vite build` = 0；`vitest run` = 25 passed（本层未改测试）
- [x] E2E 全量 **63 passed**（栈顶分支、对全新构建的 dist；`account` / `closure` /
      `history-management` / `responsive` / `help-feedback` 五个 spec 均覆盖本层改动面）
- [x] 四个既有守卫 + UI 反模式守卫 exit 0；`ruff check .` All checks passed
- [ ] CI 全量**真跑**零回归 —— 未推送，CI 未触发
- [ ] 推送分支并开 PR，回填 PR 号
- [ ] 全量 pytest 套件重跑（本轮只跑了守卫单测）
- [ ] 正式需求编号与关联 Issue 确认（现用 30 占位）

## 8. 影响范围与风险

**模块**：`web/frontend/src/components/AccountPanel.tsx`、
`src/features/account/LegalModal.tsx`（新）、`src/features/knowledge-base/ChunkPreviewModal.tsx`（新）、
`src/features/knowledge-base/useRagDocActions.ts`（新）、`src/hooks/useSideData.ts`（新）。

### 登记与实现的漂移（**必须登记，这是本轮最有价值的发现之一**）

U41 与 U25 在 `docs/web-frontend-audit.md` 里被登记为 **U25 → R2 批次**、**U41 → R3 批次**，
而 `docs/project-status.md` 记 **「R1~R7 收官」**。但代码里这两项**直到本条需求才真正修完**：

- U41：`resetLocalData` 存在（说明 R3 动过手），但清单不全 ⇒ **半修**；
- U25：`refreshSideData` 仍是裸 `Promise.all` ⇒ **未修**。

这正是 `docs/project-status.md` 开头那段「文档状态漂移」要防的情形：
**登记归入某批次 ≠ 该批次真的改完**。建议在看板里如实登记本次补修，并核对 R2/R3 是否还有同类残留。

### 拆分中探明并固化的风险点（写进新文件注释，防后人改错）

- **`locatorLabel` 差点被凭印象重写**：抽取时按记忆写的第一版有**三处**与原文不同——
  `slide` 的文案是「第 N **页**幻灯片」（不是「张」）、行范围读的是**数组** `row_range`
  （不是 `row_start`/`row_end` 两个数字字段）、兜底是「**全文**」（不是「未提供定位」）。
  **三处均无 E2E 覆盖**（`kb-chunk-list` 只断言存在性）。逐字比对才发现，
  已把「逐字搬自 AccountPanel，不要凭印象重写」写进新文件注释；
- **TDZ 陷阱**：`const preview = useChunkPreview()` 必须声明在 `resetLocalData` **之前**——
  后者要调 `preview.reset()`，而 `const` 没有提升，放到后面会直接触发 TDZ 报错；
- **`LegalModal` 的三个挂载点必须保持互斥**：`Modal` 用**模块级栈** `modalStack` 判定「最上层」，
  Escape / 焦点陷阱只由栈顶响应；若把它提成「全局只挂一次」或让两处同时挂载，
  就会多 push 一个栈项、Escape 关错层。另：落地页是 `fixed inset-0 z-50`，法律弹窗 overlay
  同为 `z-50`，靠 **DOM 顺序**取胜；调整挂载顺序会把弹窗盖住，
  而 `account.spec.ts` 只断言 `toBeVisible()`、**测不出遮挡**；
- **`useUploads` 的宿主层级不得下移**：上传状态机（XHR 进度 + 1s 轮询）必须挂在**永不卸载**的
  外层；一旦移进只在 `kbOpen` 时渲染的面板，收起面板会 `xhr.abort()` 掉**在途上传**。
  故本层只拆状态与渲染，不动该 hook 的宿主；
- **`refresh()` 永远是一次打三个接口**：不可拆成三个独立 hook 各自 mount 时再拉一次，
  否则单次触发会从 3 个请求涨到 6 个——而它被 `kb-toggle`（可连点）与「每次上传完成」频繁调起。

## 9. 测试策略

- **靠既有 E2E 反向验证「零行为改动」**（本层刻意不新增测试，因为目标是行为不变）：
  `account.spec.ts`（上传队列、KB 列表、弹窗 Escape、邀请参数一次性消费）、
  `closure.spec.ts`（登录门、上传链路、KB 两步删除、注销、会话安全）、
  `history-management.spec.ts`、`responsive.spec.ts`（窄屏不溢出）、`help-feedback.spec.ts`；
- **手工核对的硬契约**（拆分前逐项清点）：本文件声明 42 个 `data-testid`
  （39 个属性 + 3 个传给 `Modal` 的 `testId` prop），其中 23 个被 E2E 断言；
  另有 14 项被断言的中文文案/属性（配额行 `" · "` 连接、`{n} 块`、
  `已入库（N 块）` 作为 `aria-valuetext`、注销提示透出 `rag_cleanup` 原值、
  `关闭` 按钮的顺序敏感性、`window.prompt` 必须保留等）；
- 本层**未新增**测试；U41/U25 的修复由既有的登出/删除/刷新路径间接覆盖。
  ⚠️ 已知缺口：`deleteDoc` 失败提示走账号条错误通道（R6），**当前无 E2E 覆盖**
  （`closure.spec.ts` 只覆盖删除成功路径）。

## 10. 变更记录

| 日期 | 类型 | 原因 | 改动摘要 | 关联 PR/commit |
|---|---|---|---|---|
| 2026-10-08 | 重构 + 缺陷修复 | 单文件集中管理 10 个领域；U41/U25 登记归入 R2/R3 但实际未修完 | 抽出 4 个切片（LegalModal / ChunkPreviewModal / useSideData / useRagDocActions），714 → 572 行；补全 U41 清理清单；U25 改 allSettled | 待开（本地 commit `aa880a4`，分支 `chore/30-account-panel-domain-state`） |
| 2026-10-08（续） | 重构 | 剩余切片 S10 / S2 / S1 | S10 支撑弹窗 → `features/support/SupportModals.tsx`；S2 账号条 UI 状态 → `features/account/useAccountBarUi.ts`；S1 会话域状态 → `features/account/useSessionEntry.ts`；572 → 555 行 | 已合并（PR #165 / #168 / #169） |
