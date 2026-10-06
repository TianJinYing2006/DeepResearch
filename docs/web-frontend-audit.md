# 前端 UI/UX 全量审计报告

> 状态：**已交付（只读审计）**，基线 `fca0bd7`，日期 2026-09-29。
> 方法：`frontend-design-audit` 15 条可用性原则（Nielsen）+ Vercel《Web Interface Guidelines》规则，逐文件全文审查；两份并行审计合并去重。
> 范围：`web/frontend/src` 全部 9 个运行时文件 + `index.html`（测试面见 §7）。
> 边界：静态源码审计，未做浏览器渲染实测；对比度/尺寸为源码推导值。
> 配套：`docs/web-frontend-inventory.md`（结构真源）、`docs/requirements/12-frontend-ui-audit.md`（需求载体）。

## 1. 总览

| 严重度 | 数量 | 含义 |
|---|---:|---|
| 4 灾难 | 1 | 核心任务对某类用户完全不可用 |
| 3 严重 | 13 | 显著阻断/静默失败/不可读 |
| 2 次要 | 38 | 明显摩擦或无障碍降级 |
| 1 修饰 | 27 | 一致性/打磨项 |
| **合计** | **79** | |

**一句话结论**：这个前端的「状态诚实」与「错误结构」是全项目最好的部分（不伪造进度、结构化错误卡、空态齐全）；短板高度集中在三块 —— **弹窗/焦点管理、`aria-live` 动态播报、本地化格式（Intl）**，其次是**失败不毁已有状态**（启动失败清报告、分页失败清列表）与**长任务性能**。视觉设计本身问题不大，重构重点在可访问性与状态健壮性，而不是重做配色。

## 2. Quick Wins（最高性价比，建议第一轮）

1. `App.tsx:528` 成本字段可能 `undefined` → `formatCost` 抛错且无 ErrorBoundary ⇒ 白屏（**U2**）
2. 全局无任何 `aria-live`/`role="alert"`（**U3**）；错误卡、终局、复制/导出结果全部对读屏静默
3. `slate-600/700` 文案对比度 ≈1.7–2.4:1，`slate-500` ≈3.8:1，均低于 AA（**U4**）
4. 4 个 portal 弹窗无 `role="dialog"`/焦点陷阱/Escape（**U5–U7**）
5. 知识库上传控件 `display:none`，键盘完全不可达（**U1**）
6. 删号用 `window.prompt` 明文密码、无法本地化、可能被浏览器抑制（**U10**）
7. 启动失败会清掉上一份报告与恢复点（`useResearchStream.ts:196-226`，**U11**）
8. 历史「加载更多」失败会连已加载的列表一起清掉且无重试（**U12**）
9. `openReport` 无 loading、无 `try/catch`，网络失败点击无反应（**U13**）
10. `ProgressBar` 无 accessible name / valuetext，阶段无语义（**U29/U37**）

## 3. 严重度 4

**U1 · 知识库上传键盘不可达**
- 位置：`AccountPanel.tsx:545-553`
- 问题：`<input type="file" className="hidden">`（display:none）包在不可聚焦的 `<label>` 里，不进 Tab 序列、无键盘激活路径。
- 影响：键盘用户完全无法上传文档，无替代入口。
- 修复：`sr-only` + `peer-focus-visible:ring` 的可聚焦 input，或真按钮触发 `inputRef.click()`。

## 4. 严重度 3

**U2 · 终局成本缺失导致渲染崩溃（无 ErrorBoundary）**
- 位置：`App.tsx:528`（`formatCost(finished.cost_estimate_cny)`）、`App.tsx:895-899`、`main.tsx:6-9`；另 `App.tsx:527` 的 `report.length` 同理
- 问题：`formatCost(undefined|null)` 走到 `toFixed` 抛 TypeError；`isAguiEvent` 只校验 `type`，该状态可达；无 ErrorBoundary ⇒ 整树卸载白屏，刷新回放同一帧继续崩。
- 修复：`cost == null ? '—' : …`；`formatCost` 对非有限值返回 `—`；补 ErrorBoundary（U32）。

**U3 · 动态内容对读屏静默（全站零 `aria-live`）**
- 位置：`App.tsx:445-531`（错误/超时/摘要）、`422-427`（状态徽标）、`601-613`（复制/导出）
- 问题：无 `role="alert"`/`role="status"`；错误、超时、完成、复制成功都无播报。
- 修复：错误卡 `role="alert"`；状态/摘要区 `aria-live="polite" aria-atomic="true"`；复制/导出加视觉隐藏 live region。

**U4 · 低对比度文案（WCAG AA 不达标）**
- 位置：`App.tsx:678,734,745,759,773,783,792,794,343,346,428,298`、`ProgressBar.tsx:34`（slate-600/700）；`App.tsx:255,263,279,433,594,665,713,721,761,778`、`ProgressBar.tsx:46,53,78`、`ReportView.tsx:74`、`index.css:80`（slate-500 ≈3.8:1）
- 修复：正文/次要文案统一 `text-slate-400`（≈7:1）；纯装饰才允许 500+。

**U5 · 四个弹窗无 dialog 语义**
- 位置：`AccountPanel.tsx:453-477`（法律）、`479-531`（登录门）、`713-749`（邀请）、`752-770`（预览）
- 修复：统一 `<Modal>`：`role="dialog" aria-modal="true" aria-labelledby`，标题加 id。

**U6 · 无焦点陷阱，背景应用仍可操作**
- 位置：同上；`App.tsx:245-689` 保持挂载
- 影响：未登录用户可 Tab 到「开始研究」并提交（401）；焦点逃逸。
- 修复：Tab 循环 + 打开时对背景 `inert`/`aria-hidden`。

**U7 · 可关闭弹窗无 Escape 处理**
- 位置：`AccountPanel.tsx:453-477`、`713-749`、`752-770`（无任何 `onKeyDown`）
- 修复：共享 Modal 内置 Escape → 同一 close handler；遮罩点击同理。

**U8 · 认证错误不播报、不关联字段**
- 位置：`AccountPanel.tsx:508-510`、`734`；输入 `493-507`、`724-733`
- 修复：`role="alert"` + `aria-invalid` + `aria-describedby` + 聚焦首个错误字段。

**U9 · 上传队列/进度对 AT 不可见**
- 位置：`AccountPanel.tsx:645-690`（progressbar `670-673`；非 uploading 时无 `aria-valuenow`）
- 修复：`aria-label={item.name}` + `aria-valuetext={uploadLabel(item)}`；队列包 `aria-live="polite"`；终态不再保留 progressbar 角色。

**U10 · 删号确认用 `window.prompt`**
- 位置：`AccountPanel.tsx:329-350`（prompt 在 330；错误写进无关的 `uploadState`）
- 问题：明文密码、不可样式化/本地化、被抑制时永远无法删号、错误位置错。
- 修复：应用内密码确认弹窗（输入 `DELETE` 二次确认 + 内联错误 + busy 禁用）。

**U11 · 启动失败摧毁上一份报告与恢复点**
- 位置：`useResearchStream.ts:196-199`（先清 state）、`:220-226`（catch 里 `clearStoredRun`）
- 修复：POST 成功前保留旧 state；失败时不清理 stored run，提供「返回上一份报告」。

**U12 · 分页失败清空整个历史列表且无重试**
- 位置：`AccountPanel.tsx:278-280`、渲染守卫 `596-631`
- 修复：append 失败保留已加载列表，在列表上方显示内联错误 + 「重试本页」。

**U13 · 报告预览无 pending 反馈、网络错误被吞**
- 位置：`AccountPanel.tsx:302-311`（无 `try/catch`）、`616-619`（无禁用）
- 修复：`previewLoading` + `try/catch` + 防重复请求（U38）。

**U14 · ProgressBar 无语义名与阶段语义**
- 位置：`ProgressBar.tsx:15-38`（`aria-label` 挂在无 role 的 div）、`:57`
- 修复：`role="list"/"listitem"`、`aria-current="step"`、`aria-valuetext`、非颜色完成标记。

## 5. 严重度 2（按主题，含位置与修复要点）

### 5.1 焦点与键盘
- **U15** 弹窗无初始焦点、关闭后不归还焦点（`AccountPanel.tsx:479-531,713-749,752-770`）→ 打开聚焦首字段/关闭按钮，关闭还原 trigger。
- **U16** 滚动区不可聚焦：sticky aside / RAG 命中列表 / 时间线 / 报告宽表（`App.tsx:274,396,558`、`ReportView.tsx:63`）→ `tabIndex=0` + `role="region"` + 名称。
- **U17** 档位 radiogroup 无方向键/roving tabindex（`App.tsx:310-341`）→ 补 APG 行为或改原生 radio。
- **U18** 焦点环过弱：`.field-control` 的 ring 透明度 10%、按钮类无 `focus-visible`（`index.css:80,83-93`）→ 统一 `focus-visible:ring-2 ring-emerald-300/70`。
- **U19** 面板 toggle 无 `aria-expanded/aria-controls`（`AccountPanel.tsx:541-544,554-557`）；面板/弹窗标题层级混乱（`:574,638` 是 span；`App.tsx:430` 报告内 h1 嵌套）。
- **U20** 无 skip link（`App.tsx:245-273`）。

### 5.2 错误与恢复
- **U21** 会话检查：in-flight 时误显「未登录（本地模式）」且 gate 闪烁（`AccountPanel.tsx:164,192-229,533-569`）→ checked 前显示中性「正在检查登录状态…」。
- **U22** `/api/auth/session` 5xx/网络失败与「已登出」不可区分（`:192-206`）→ 仅 401/403 置登出，5xx 显示重试。
- **U23** 登出无 `try/catch`/`response.ok`，失败时本地与服务端状态不一致（`:262-268`）。
- **U24** 未登录仍请求 quota/docs（`:226-229`）→ gate 状态下跳过。
- **U25** `refreshSideData` 无错误处理且 `Promise.all` 耦合（`:208-220`）→ `allSettled` + 分资源错误态 + 刷新按钮 busy。
- **U26** 历史筛选响应竞态（`:270-292,581-585`）→ 请求序号/AbortController，丢弃过期响应。
- **U27** `starting` 阶段无法取消、启动请求无超时（`useResearchStream.ts:187-227`、`App.tsx:355-364`）→ AbortController + starting 可停。
- **U28** 取消失败的旧错误在终局后仍显示（`useResearchStream.ts:263-278` vs `149-161`）→ 终局清 error。
- **U29** 未知/畸形事件终止整条流（`useResearchStream.ts:144-175`、`agui.ts:158-162`）→ 跳过并记录未知帧；按类型校验 payload。
- **U30** SSE `onerror` 一律显示「重连中」，永久 404 会无限等待（`useResearchStream.ts:181-184`）→ `readyState===CLOSED` 时给终态错误 + 重开新研究。
- **U31** 复制正文无 `try/catch`（`App.tsx:214-219`）。
- **U32** 无 ErrorBoundary（`main.tsx:6-10`）。
- **U33** 导出无 in-flight、失败提示 1.6s 即消失、Blob URL 同步 revoke 且 anchor 未入 DOM（`App.tsx:223-243,605-613,232-237`）。
- **U34** 终止类操作再无入口：「用同样参数重试」只在错误卡（超时/取消/完成后没有）（`App.tsx:487-497,503-517`）。
- **U35** 历史空/错误态无 CTA、无内联重试（`AccountPanel.tsx:596-598,693-695`）。
- **U36** 配额获取失败只消失不提示（`:210-211,535-540`）。

### 5.3 状态安全与一致性
- **U37** 「停止研究」单击即毁掉长任务剩余工作，无确认/撤销（`App.tsx:360-364`）→ 两步确认或确认弹窗。
- **U38** 报告预览可并发触发、无禁用（`AccountPanel.tsx:616-619`）。
- **U39** 上传失败不能重试/移除，排队/上传中不能取消（`AccountPanel.tsx:376-411,645-690`）。
- **U40** 轮询无清理（卸载/登出后仍跑 30s），失败早期返回不清 `uploadFilesRef`（`:352-370,376-411`）。
- **U41** 登出/注销不清 KB/历史状态；注销成功提示被 gate 分支吞掉（`:262-268,343-346,479-531`）。
- **U42** `?invite=` 未消费，已登录刷新仍弹注册（`:178-180,713`）→ 读取后 `replaceState` 清除 + `inviteOpen && !user`。
- **U43** 草稿不持久化、无 `beforeunload` 提醒（`App.tsx:61-62,286-307`）。
- **U44** URL 不反映 run 状态，报告不可分享/书签，Back 直接离开（`App.tsx:75-89,428`）→ `?run=` + `document.title`。
- **U45** 状态筛选缺 `QUEUED/CANCEL_REQUESTED/CREATED/LOST` 选项（`AccountPanel.tsx:587-592` vs `127-137`）。
- **U46** 文档列表 key 可能冲突（`:699`）→ `doc_id ?? source-index`。

### 5.4 触控与布局
- **U47** 触控目标 22–28px（账户条 pills/筛选/刷新/关闭），无 `touch-action: manipulation`（`AccountPanel.tsx:541-557,577,639,662-667,719-721`；`App.tsx:333,489,601,606`）。
- **U48** 弹窗不锁背景滚动、无 `overscroll-contain`（`AccountPanel.tsx:455,482-485,714,753`）。
- **U49** 失败上传显示 100% 红条，视觉上等于「已完成」（`:650-657`）→ 错误用不确定/空条 + 图标。

### 5.5 性能
- **U50** `events` 无界增长 + 每次事件全量 `summarize` + 250ms tick 导致全树重渲（`useResearchStream.ts:147,280`；`App.tsx:132-140,151-159,558-562`）→ 增量进度、行 memo、列表虚拟化、时钟下沉到叶子组件、`document.hidden` 暂停。
- **U51** 长列表未虚拟化：时间线/引用/来源、历史 >50（`App.tsx:558-562,637-652`；`AccountPanel.tsx:600-631`）。
- **U52** 进度条动画 `transition-[width]`（layout 属性）（`App.tsx` 上传条 `AccountPanel.tsx:675`、`ProgressBar.tsx:59`）→ `transform: scaleX()`。

### 5.6 本地化与文案
- **U53** `created_at` 原始 ISO 切片，无时区/本地化/`<time>`（`AccountPanel.tsx:615`）。
- **U54** 配额/成本手写 `¥`+`toFixed`，无 `Intl.NumberFormat`、无 `tabular-nums`（`:445-447,535-540`；与 `App.tsx:895-899` 精度不一致）。
- **U55** 技术字符串直出：`HTTP n`、`rag_cleanup` 原值、snake_case 统计键、`event.type`、`JSON.stringify`（`AccountPanel.tsx:58,101,346`；`App.tsx:457-469,663,678,759,883`）。
- **U56** 中英混排 eyebrow（AG-UI 事件语义/Live/Evidence/Reflection/Audit…）（`App.tsx:264,282,521,553,567,592,629,648,657,674`）。
- **U57** 同一概念三种叫法：文档摄取 / RAG 文档 / 知识库（`App.tsx:385,410-412` vs `AccountPanel.tsx:545,556`）→ 统一「知识库」。
- **U58** 时长格式不一致（`2m 13s` vs `2 分 13 秒`）（`App.tsx:901-908` vs `ProgressBar.tsx:94-99`）。

### 5.7 内容呈现
- **U59** `SourceLink` URL 截断无 title/展开/复制（`App.tsx:398-401,799-803`）。
- **U60** 报告 Markdown 图片无 `max-width`/尺寸约束（`index.css` 无 `.report-prose img`）→ 移动端溢出 + CLS。
- **U61** 「运行摘要」与 4 指标卡重复呈现同组数字（`App.tsx:519-531` vs `533-549`）→ 合并或终局后隐藏指标卡。

## 6. 严重度 1（修饰/健壮性，27 项摘要）

- 占位符/加载文案缺省略号（`App.tsx:292,304,356,362`）；字数计数器缺失（`:294,306`）
- 报告正文固定 px 字号（`index.css:96`）；10–11px 小字过多（`App.tsx:281,457,576,663,731,759`；`AccountPanel.tsx` 多处 `text-[11px]`）
- 指标卡 4 种强调色并列无主次（`App.tsx:534-548`）；时间线 hover 高亮但不可点（`:754`）
- 装饰符号未 `aria-hidden`（`:250-252,743,696`）
- `theme-color` 与页面背景不一致（`index.html:6` vs `index.css:19,30`）；无 favicon（`index.html:3-9`）；首屏空白无 loader（`index.html:10-11`）
- 引用/来源无筛选/搜索（`:627-670`）；报告无目录锚点（`ReportView.tsx:55-71`）
- `ReportView` chunk 重置用 effect，存在同步大解析隐患（`ReportView.tsx:40-50`）→ `key` 重置
- 档位高亮在默认档缺失时错位（`App.tsx:110-113,316-323`）
- 无 `aria-busy`（`AccountPanel.tsx:511-513,735-737,597,625-631,694`）；inputs 缺 `name`（`:493-505,724-733`）；密码可见切换缺失（`:496-500,727-730`）
- `formatBytes` 硬编码单位（`:104-108`）；`summarize` 默认 20 hops 属编造数据（`progress.ts:107`）；ETA 末尾消失/误导（`progress.ts:88-91`、`ProgressBar.tsx:86-92`）
- 邀请弹窗关闭即丢输入（`:719-721`）；深色 select 选项未显式配色（`:577-593`）；邀请码字段缺 `autoComplete="off"/spellCheck/inputMode`（`:504-505,732-733`）；上传无约束提示/拖拽（`:545-553`）
- 无密码重置路径提示（`:479-531`）
- `csrfHeaders` 两处重复（`AccountPanel.tsx:42-45` vs `useResearchStream.ts:16-19`）；三个 portal/两套注册表单重复（`:453-477 vs 752-770`；`:486-527 vs 716-746`）
- StrictMode 下 dev 双请求（`main.tsx:7`）——生产无影响

## 7. 原则覆盖与强项

**覆盖结论**：H1–H15 全部有发现也有强项；最弱 H13（可访问性，26 项）、H9（错误恢复，10 项）、H1（状态可见，15 项）；最强 H5（错误预防：禁用/必填/服务端固化档位）、H12（结构：地标/卡片分组）。

**代表性强项（勿在重构中弄丢）**
1. 诚实进度：终局前 99% 封顶、ETA 标注估算、不伪造摄取百分比（`progress.ts:3-13`、`ProgressBar.tsx:91`）
2. 结构化错误卡：code/component/node/hint/details + 同参重试（`App.tsx:445-501`）
3. 全站空态带下一步文案（`:555-569,617-623,633-641`）
4. 全局 `prefers-reduced-motion`（`index.css:194-203`）
5. 状态从不只靠颜色（徽标/文案双通道）
6. 长内容处理：`min-w-0`+`truncate`+`overflow-wrap:anywhere`+宽表滚动容器
7. 安全基础：`lang`、可缩放 viewport、`color-scheme: dark`、表单 label/`autocomplete`/`required`

**反模式扫描**：`div` 点击、无 label 输入、无 aria-label 图标按钮、`transition: all`、`onPaste` 阻断、`user-scalable=no` —— 均为 0 违规；违规项集中在：无 `aria-live`、modal 无语义、`window.prompt` 确认、硬编码日期/数字格式、未虚拟化长列表。

## 8. 建议实施顺序（对接 `docs/web-frontend-inventory.md` §7）

| 批次 | 内容 | 对应发现 |
|---|---|---|
| R1（本次重构前置，小 PR） | ErrorBoundary；成本/长度空值防护；`aria-live`/`role=alert`；对比度 token 统一；modal 组件（语义+焦点陷阱+Escape+滚动锁） | U2–U7,U32 |
| R2（基础设施 PR） | `lib/api.ts`（fetch/CSRF/错误/Abort）；`lib/format.ts`（Intl 日期/货币/字节）；`types/` 收敛；死代码清理 | U24–U33,U53–U54,U58 |
| R3（状态安全 PR） | 启动失败保状态；历史分页失败保列表；预览 loading；上传队列重试/移除/取消 + 轮询清理；草稿持久化 | U11–U13,U38–U43 |
| R4（结构拆分 PR） | 按盘点 §7 拆 App/AccountPanel；拆分同时落 a11y（radiogroup、面板标题层级、skip link、滚动区可聚焦） | U15–U20,U37,U47–U49 |
| R5（性能 PR） | 增量进度、行 memo、虚拟化、时钟下沉、`transform: scaleX` 进度动画 | U50–U52 |
| R6（视觉重设计） | `frontend-design` 定方向 + `baseline-ui` 落地 + 文案/术语统一 | U55–U58,U61 + S1 视觉项 |
| R7（收口，**已实施**） | 补 e2e 6 条（`closure.spec.ts`：登录门/超时/配额/键盘上传/KB 删除/注销）；修复 U1（上传输入 `sr-only` 键盘可达）；新增 KB 文档删除 UI（两步内联确认 + `DELETE /api/rag/docs`）；回填需求 12 DoD。截图基线**不做**（中文字体/渲染跨环境不稳定，另行提案） | §7 测试缺口 |

> 每个批次独立 PR，过 `tsc`+`build`+Playwright 全绿；`data-testid` 与中文文案契约（盘点 §6）不得破坏。
