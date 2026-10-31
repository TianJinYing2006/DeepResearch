# 需求-41-streaming-report-output（报告正文逐字流式输出）

> **状态：草稿 · 未立项**（2026-10-31 登记，来源：用户提出「论文输出改为流式输出，类似 DeepSeek 那样逐字输出」）。
> 未立项阶段只记录范围、边界与未决问题；**不排期、不写代码**，立项时按本模板展开实施。
> 飞书镜像：不建（未立项阶段不占飞书文档位；立项后按模板创建）。
> 关联 Issue：[#313](https://github.com/TianJinYing2006/DeepResearch/issues/313)

## 1. 元信息

| 项 | 值 |
|---|---|
| 编号 | 41 |
| 标题 | 报告正文逐字流式输出（类 DeepSeek 逐字/增量渲染） |
| 优先级 | P1（候选，未立项） |
| 状态 | 草稿 |
| 负责人 | TianJinYing2006 |
| 关联 Issue | #313 |
| 关联 PR | 待立项后建（复用需求 35 R4 通道，不新建协议） |
| 创建 / 更新 | 2026-10-31 |

## 2. 问题背景

- 用户诉求：**「论文输出改为流式输出，类似 DeepSeek 那样逐字输出」**——希望报告正文像对话模型一样边生成边逐字出现，而不是等待或整块刷新。
- 现状不是「没有流式」：需求 35 R4（2026-10-13，PR #207）已交付报告增量通道——`LLMClient.chat_stream` + Writer 增量 → `REPORT_DELTA`/`REPORT_RESET` → SSE → 前端预览卡渐进渲染。
- 但用户感知仍是「非流式」，差距在**粒度与呈现**：
  - 后端按 **400 字符**合并一条 `REPORT_DELTA`（`DR_REPORT_STREAM_FLUSH_CHARS` 默认 400）⇒ 视觉上是每 400 字「跳一段」；
  - 生成期是「预览卡」（`ReportPreviewCard`），终局是「报告卡」（`ReportCard`），两套视图 + 终局整段替换 ⇒ 有跳变感，不像打字机；
  - 服务端还有总开关 `DR_REPORT_STREAM_ENABLED`（默认 true；关掉就只剩节点级事件），环境差异会造成「有的环境看不到流式」。
- 用户已经看到报告生成耗时较长，如果正文不能边写边看，体验会显著差于对话式产品。

## 3. 需求分析

**目标**：在**不改 Writer 正文生成逻辑、不改终局质量判定、不新建协议**的前提下，把报告生成期呈现从「400 字一块」收敛到「接近逐字的连续输出」，并让预览与终局体验连贯。

**要解决的具体问题**：

1. **粒度**：正文增量按更细粒度可见（逐 token 或 ≤ 几十毫秒/几十字符批量），同时事件量、落库行数、断线回放成本可控；
2. **渲染**：前端渐进渲染 Markdown，不能每个 delta 全量重解析数万字（性能），也不能破坏引用编号/链接/表格；
3. **连贯**：生成期与终局用同一视图语言（避免预览卡→报告卡跳变），终局替换不闪烁、不丢失已显示的引用与滚动位置；
4. **协议不变式**：`REPORT_RESET`（开始/半截失败/重试）语义不变；`{text, offset}` 可校验；`Last-Event-ID` 回放一致；flagged 脱敏沿用；
5. **可回退**：默认粒度可配置，环境变量可回到现状（400 字符），不影响分享页/导出/PDF。

**成功定义（可量化）**：

- 报告生成期正文可见更新间隔 ≤ 100ms 量级（视觉连续，非块状跳变）；
- 断线重连后回放内容与实时内容一致（offset 校验通过，无重复/缺失）；
- 5 万字级报告渲染不掉帧（前端增量解析耗时可控，单次增量 < 16ms 目标）；
- 事件行数有上限保护（如单 run `REPORT_DELTA` ≤ N 条，超过则动态合并）；
- `prefers-reduced-motion` 用户与视觉基线截图不受打字动画影响。

## 4. 当前设计

| 位置 | 现状 | 痛点 |
|---|---|---|
| `research_engine/agents/writer.py:122` | `report_stream_active()` 为真时走 `smart_chat_stream(..., on_delta=emit_report_delta)`，失败降级非流式重试（`stream failed, retry non-stream`） | 增量源头是 token 级；**后端缓冲把它变粗** |
| `research_engine/llm/client.py:165` | `chat_stream()` 发 DashScope OpenAI 兼容 `stream=True`，逐 chunk 回调 `on_delta(text)` | 无问题（token 级已具备） |
| `research_engine/report_stream.py` | contextvar sink（与 usage 同构），Writer 增量进 sink | 旁路设计已闭合，无需重做 |
| `web/backend/report_events.py:24` | `flush_chars`（默认 400）合并 → `REPORT_DELTA {text, offset}` 落 `run_events`；`REPORT_RESET` 先冲刷 | **粒度 400 字符 = 用户感知的非流式主因**；事件少但太粗 |
| `config.py:ReportStreamConfig` | `DR_REPORT_STREAM_ENABLED`（默认 true）、`DR_REPORT_STREAM_FLUSH_CHARS`（默认 400） | 需要「更细粒度 + 时间窗口」的可配口径与上限保护 |
| `web/backend/main.py:3426` | `GET /api/research/{run_id}/stream`（SSE，`Last-Event-ID` 回放） | 回放成本与事件数成正比 ⇒ 粒度不能无脑降到 1 |
| `web/backend/agui.py:30` | `REPORT_DELTA`（payload `{text, offset}`）、`REPORT_RESET` | 协议不变，只调参数与前端呈现 |
| `web/frontend/src/hooks/useResearchStream.ts:73` | `reportPreview` 状态，`applyReportEvent` 折叠 delta/reset | 已有基础，粒度粗导致渲染跳变 |
| `web/frontend/src/features/report/ReportPanels.tsx` | `ReportPreviewCard`（生成中预览，终局由 `ReportCard` 整段替换） | 两套卡片视觉跳变；非打字机观感 |
| `web/frontend/src/components/ReportView.tsx` | `ReactMarkdown` + `CHUNK_CHARS` 分段渲染（超长报告先渲染前段） | 每 delta 重渲染成本高；需要「稳定前缀 + 尾部降级」策略 |

## 5. 优化方案

**后端（协议不变，只调粒度与保护）**

1. `flush_chars` 默认从 400 降到小值（如 24~64），或改为**时间窗口合并**：每 `flush_ms`（如 50ms）或累计 `flush_chars` 先到者发一条，保证视觉连续且事件量可控。
2. **事件上限保护**：按报告长度动态合并（报告越长、单条越大），并设单 run `REPORT_DELTA` 上限（如 ≤ 2000 条）；超限后按更粗粒度合并，记录一条内部指标。
3. `REPORT_RESET`、半截失败、终局前强制冲刷、重试降级、flagged 脱敏**全部沿用 R4 现有语义**，不改协议字段。
4. `/api/options`/运维文档同步 `DR_REPORT_STREAM_FLUSH_MS` 等新口径；`.env.example` 写清新默认值与回退方式。

**前端（渐进渲染 + 打字机观感）**

1. 增量渲染节流：`requestAnimationFrame`/`setTimeout` 合并多个 delta 再 setState；正文渲染策略 =**稳定前缀按 Markdown 渲染 + 尾部未闭合块先纯文本降级**（闭合后再解析），避免每次全量 `ReactMarkdown`。
2. 生成期与终局共用同一报告容器（预览卡与报告卡收敛为同一组件/同一 DOM 位置），终局替换时保持滚动位置与已渲染内容，做平滑过渡而非整块重现。
3. 打字机光标仅作视觉提示；`prefers-reduced-motion: reduce` 时直接显示（与现有视觉基线冻结策略一致）。
4. 自动滚动：仅当用户未手动上滚时贴底；用户上滚后暂停自动滚动，避免「抢滚动」。
5. 引用/链接点击仍在终局后可用（生成期禁用或延后），避免半截引用跳错。

**不做 / 边界**

- 不改 Writer 正文生成、引用编号规则、终局质量门与评测口径；
- 不新建 SSE 通道/协议、不改成 WebSocket；
- 不把「逐字」做成前端假动画（必须以真实 token 增量为源，避免终局对不上）；
- 不动分享页/导出（md/PDF/DOCX）行为。

## 6. 设计策略

- **协议优先复用**：R4 已定义 `REPORT_DELTA/REPORT_RESET/offset/Last-Event-ID`，本需求只调粒度与呈现，避免第二次协议迁移。
- **成本权衡**：事件粒度 × 报告长度 = 落库行数与回放带宽；默认值要选「视觉连续 + 事件量可接受」的点，并提供动态合并上限。
- **性能**：前端增量解析必须避免 O(n²) 级重渲染；以「稳定前缀 + 尾部降级」为核心策略。
- **视觉基线**：现有 e2e 视觉基线依赖 `reducedMotion:'reduce'` 冻结 JS 打字效果；本需求需确认基线不受影响或同步更新基线。
- **可回退**：`DR_REPORT_STREAM_FLUSH_CHARS`/新 `FLUSH_MS` 回到旧值即近似现状；`DR_REPORT_STREAM_ENABLED=false` 仍可完全关闭。

## 7. 验收标准（DoD）

- [ ] 生成期正文增量视觉连续（更新间隔 ≤ 100ms 量级，不再 400 字一块）；
- [ ] `REPORT_RESET`/半截失败/重试/回放语义不变，单测与端到端仍全绿；
- [ ] 断线重连（`Last-Event-ID`）回放内容与实时内容一致（offset 校验、无重复/缺失）；
- [ ] 5 万字级报告渲染性能达标（增量解析单次 < 16ms 目标；无掉帧级卡顿）；
- [ ] 预览与终局收敛为同一视图，终局替换无闪烁/滚动丢失；
- [ ] `prefers-reduced-motion` 直出且视觉基线通过（或同步更新基线并说明）；
- [ ] 事件量上限保护生效（单 run `REPORT_DELTA` 有上限，超限动态合并）；
- [ ] 分享页/导出 md/PDF/DOCX 不受影响；
- [ ] 运维文档/`.env.example`/需求 35 §R4 附注同步；CHANGELOG 记录。

## 8. 影响范围与风险

| 风险 | 说明 | 缓解 |
|---|---|---|
| 事件量膨胀 | 粒度变细 → `run_events` 行数与 SSE 回放成本上升 | 动态合并 + 单 run 上限 + 默认值压测 |
| 前端性能 | 数万字 Markdown 高频重解析 | 稳定前缀渲染 + 尾部降级 + 节流 |
| 半截文本一致性 | 重试/失败时前端拼出重复文本 | 沿用 `REPORT_RESET` 协议，补 mutation 测试 |
| 视觉基线冲突 | 打字动画影响 e2e 截图 | reduced-motion 直出；必要时更新金标准并登记 |
| 回放差异 | 回放与实时 offset 不一致 | offset 校验 + 重连单测（既有用例扩展） |
| 移动端 | 低端设备高频渲染卡顿 | 节流 + 增量策略；staging 实测后再定默认粒度 |

**影响模块（预计）**：`web/backend/report_events.py`、`config.py`（ReportStreamConfig）、前端 `useResearchStream`/`reportPreview`/`ReportPanels`/`ReportView`/`App`；**不动**：`writer.py` 生成逻辑、引用编号、终局判定、导出/分享。

## 9. 测试策略

- 后端单测：flush 粒度（字符/时间窗口取先到者）、reset 冲刷、半截失败再 reset、终局前冲刷、事件上限动态合并、flagged 脱敏回放；
- 前端单测：`applyReportEvent` 既有语义不变；节流合并、尾部降级渲染、reduced-motion 直出、自动滚动策略；
- 集成/e2e：staging 真实 run 观察 delta 到达间隔与渲染连续性；断线重连回放一致性；视觉基线（`reducedMotion`）；
- 性能：构造 5 万字报告回放/实时渲染的耗时测量（本地或 staging，记录口径）；
- 回归：分享页、导出 md/PDF/DOCX e2e 全绿。

## 10. 变更记录

| 日期 | 类型 | 原因 | 改动摘要 | 关联 PR/commit |
|---|---|---|---|---|
| 2026-10-31 | 文档 | 用户提出「论文输出改为流式输出，类似 DeepSeek 那样逐字输出」 | 建档为**草稿·未立项**：盘点需求 35 R4 现状（400 字符粒度）与差距，记录方案、边界、DoD 与未决问题；Issue #313 | 本文档 / #313 |

## 11. 未决问题（立项前拍板）

1. **粒度默认值**：字符窗口（如 24/64 字符）还是时间窗口（如 50ms）？建议「时间窗口 + 字符上限」双条件，默认 50ms/64 字符。
2. **逐字 vs 定时批**：是否需要真正的 token 级（每条 delta ≤ 几个 token），还是 50ms 批量已足够「类 DeepSeek」？建议后者，先压测事件量。
3. **预览与终局是否合并为一个组件**：合并改动面稍大但体验最好；建议立项时按「同一容器 + 终局替换」实施。
4. **Markdown 增量渲染策略**：尾部未闭合块降级渲染的实现成本 vs 收益；建议先做降级版，再评估成熟库。
5. **staging 现状核对**：确认 staging 的 `DR_REPORT_STREAM_*` 是否与默认一致（若被关闭，用户自然看不到流式）；立项时先核对再谈优化。
6. **视觉基线**：现有金标准是否因光标/动画变化需要更新；建议 `reducedMotion` 直出保持基线不动。
