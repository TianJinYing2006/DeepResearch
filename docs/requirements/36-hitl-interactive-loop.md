# 需求-36-hitl-interactive-loop

> 状态流转：草稿 → 进行中 → 自测 → 待合 → 已合
> **性质**：**已立项（批次 3）**；开工登记见 §12.6（主理人 2026-10-13 确认按本需求开工；TBD-0~7 按 §5 推荐口径 + §12 评审修订执行）。
> **来源**：`docs/extension-directions-survey.md` §0 结论③ / §3 方向 A / §4 批次 3（调研建议 P0 主线）。
> **竞品参照**（调研 §1.1~§1.3）：open_deep_research `clarify_with_user`（Scoping 澄清）；Gemini Deep Research `collaborative_planning`（多轮改计划后批准）；DeerFlow 2.0 自然语言改计划。
> **前置（均已交付）**：R3 跨进程 checkpoint（`thread_id=run_id`；`fresh/resumed/replayed`，需求 35）＝ `interrupt` 现成载体；R2 evidence manifest ＝ 追问证据池基础。
> **纪律**：① 不改核心判定口径（W8 基线可比性）；② **eval/CI 必须可关**（沿用需求 7 TBD-1d 硬约束）；③ 所有决策点进 `run_events`（审计即事件）。
> 飞书镜像：待建（立项拍板后同步 `DeepResearch 需求文档` 文件夹）。

## 1. 元信息
| 项 | 值 |
|---|---|
| 编号 | 36 |
| 标题 | 批次 3「HITL 交互闭环」：澄清提问（≤2 轮）+ 计划审批（interrupt）+ 报告后追问（不重搜） |
| 优先级 | P0 |
| 状态 | 自测 |
| 负责人 | TianJinYing2006 |
| 关联 Issue | #210 |
| 关联 PR | #211（P1）/ #212（P2，堆叠）/ #213（P3，堆叠） |
| 创建 / 更新 | 2026-10-13 |
| 前置评审 | TBD-0~7 按 §5 推荐 + §12 评审修订执行；开工登记见 §12.6 |

## 2. 问题背景

- **诊断（调研 §0①/§2）**：「功能单一」的准确症状是**交互闭环缺失**——现状 `提交 → 进度 → 取消 → 报告`，四个动作之间没有任何「人参与决策」的点：无澄清、无计划审批、无报告后追问（缺口表列 🔴 大）。
- **历史挂账**：澄清范围曾在需求 7 以 **TBD-1d 冻结**（"设计已定、实现挂 W8+"），冻结理由＝① eval 批量跑必须强制关闭 ⇒ 收益无法量化；② 需改图结构（clarify 节点 + 条件边 + CLI/Web 交互层，≈4~6h）且承担 W1 收敛单测回归风险。**R3 交付后成本结构已变**：checkpoint 已跨进程（`web/backend/worker.py:130-183`），`iter_run` 已支持 `resume`（`research_engine/graph.py:637-639`）——interrupt 载体就位，本立项即 TBD-1d 的解冻落地。
- **代码现状（实测事实）**：
  - 图是 8 节点单图、entry=`plan`（`research_engine/graph.py:137-168`）：plan 产出即自动流入 research，**没有任何停顿点**；全仓无 `interrupt` / `Command` / `AWAITING_*` / clarify / approval 代码（唯一 `interrupted` 是前端工作流展示语义，与此无关）。
  - 状态机 9 态由 DB CHECK 锁定（`migrations/0001_runs_and_events.sql:16-18`）：`CREATED/QUEUED/RUNNING/CANCEL_REQUESTED/SUCCEEDED/FAILED/CANCELLED/TIMED_OUT/LOST`——**没有「等待人类」的合法状态**。
  - 租约清扫只扫 `RUNNING/CANCEL_REQUESTED`（`web/backend/store.py:910-915`）；`ACTIVE_STATUSES`（`web/backend/persistence.py:23-24`）同时驱动：并发/配额计数（`store.py:827-836`）、SSE 尾随终止（`main.py:2495`）、归档守卫、启动兜底 `mark_stale_as_lost`——新状态若不登记：既不被清扫、也不被识别为「活跃」，等待中的 run 会被误判/漏管。
  - 事件面已齐：AG-UI 帧集合（`web/backend/agui.py:16-33`，含 R4 的 `REPORT_DELTA/RESET`）+ `run_events` 持久化 + `Last-Event-ID` 断线重连（`main.py:2460-2505`）。
  - 追问基础已就位：`findings/working_findings`（`state.py:187-196`）、终局 `evidence_index`、`evidence_manifest` 产物（`web/backend/persistence.py:111-115`）、网页快照表（迁移 `0022`）。
  - 提交入口 `StartRequest` 仅 `topic/instructions/profile`（`main.py:425-442`）；档位 `quick/standard` 可选（`profiles.py:43`）。
- **面试讲述价值（调研 §3）**：`interrupt` + checkpoint + 状态恢复是 LangGraph 核心考点；「为什么要在 plan 之后停」是设计题；等待态与租约/清扫的相互作用是工程深水区。

## 3. 需求分析

目标（一句话）：**把「提交 → 黑盒运行 → 交付」升级为「澄清 → 计划可见可改 → 批准执行 → 交付 → 基于证据追问」，全程可审计、可关闭（eval/CI 强制关）。**

- **H1 澄清提问（≤2 轮）**：提交后、规划前，LLM 判定范围清晰度；不清晰则 `interrupt` 提问（可一次含 1~3 问）；用户回答经 `Command(resume=...)` 注入；解答作为**用户确认过的参照标准**并入 `research_spec`（planner/critic/writer 同一份口径，不新造第二份）；轮次上限 2；跳过/等待策略见 TBD-3。
- **H2 计划审批**：`plan` 产出后 `interrupt` 停等（呈现子问题清单 + research_spec 摘要）；用户**批准** ⇒ resume 继续；**自然语言反馈** ⇒ planner 修订计划（≤2 轮）再停；决策（批准/修改/轮次）全部落 `run_events`；修订消耗计入 run 预算。
- **H3 报告后追问（不重搜）**：run 终局且报告可用后，基于**已有证据池**（manifest + working findings + 可取得窗口）回答追问；回答只允许引用证据池内 ID（零模型白名单校验，不合格标注）；LLM 调用经 `usage_ledger` 记账（`kind=llm`，关联 `run_id`）；不创建子 run。
- **边界（本批不做）**：报告块编辑（DeerFlow tiptap 式）不做；跨 run 记忆/研究档案（方向 E）不做；计划的结构化表单编辑不做（本批 = 自然语言反馈）；澄清/审批的二次确认（如审批后反悔）不做（取消 + retry 已覆盖）。

## 4. 当前设计（代码现状）

| 层 | 位置 | 现状 | 本批次处置 |
|---|---|---|---|
| 图拓扑 | `graph.py:137-168` | 单图 8 节点、entry=`plan`、无停顿点 | **H1/H2**：clarify 节点 + 审批门（关闭态条件边直连，节点序列零变化） |
| 恢复载体 | `graph.py:608-652`、`worker.py:130-183` | R3：`resume=True ⇒ stream(None)`；`fresh/resumed/replayed` | **H1/H2**：resume 需携带用户回答（`Command(resume=...)`），扩展流式出口与执行模式语义 |
| 流式出口 | `graph.py:654-711`、`streaming.py:37-67` | `RunStep` 无 interrupt 表示，节点取自 `progress[-1]` | **H1/H2**：识别 `__interrupt__` 载荷 ⇒ 新增「挂起」出口（非 error/非 cancel） |
| 状态机 | `migrations/0001:16-18`、`persistence.py:23-24` | 9 态 CHECK；`ACTIVE_STATUSES` 四态 | **H1/H2**：新增 `AWAITING_CLARIFICATION`/`AWAITING_PLAN_APPROVAL`（迁移 `0023`，只增） |
| 租约/清扫 | `store.py:884-941`、`store.py:571-599` | sweep 只扫 `RUNNING/CANCEL_REQUESTED`；取消对未匹配状态幂等返回 | **H1/H2**：等待态无租约、不 requeue；取消从等待态 ⇒ `CANCELLED`；等待时钟暂停 |
| 提交/配额 | `main.py:425-442`、`store.py:827-836` | `StartRequest` 无交互字段；active 计数含全部 `ACTIVE_STATUSES` | **TBD-2**：等待态的并发/每日次数口径单点定义 |
| 事件协议 | `agui.py:16-33` | 8 类帧 + `run_events` 持久化 + 重连 | **TBD-6**：新增 HITL 事件（持久化，卡片可重建） |
| 追问基础 | `persistence.py:111-115`、`worker.py:697-727` | manifest + 终局 result 已落库 | **H3**：新出口 `POST …/follow-up`（证据池 grounding） |
| 前端 | `useResearchStream.ts`、`RunPanels.tsx` | SSE 消费 + 进度/报告卡；`Modal` 基础组件可复用 | **TBD-6**：澄清卡/计划卡/追问框 + testid 契约 + e2e |
| 评测/CI | `conftest.py:21-38`、`provenance.py:118-144` | 零 LLM/零网络隔离模式已有先例 | **TBD-5**：三开关默认关/强制关 + provenance 记录 |

## 5. 优化方案（TBD 推荐 + 待拍板）

- **TBD-0（前置，P0）before 基线 —— 推荐：不采，以「关闭态零漂移」确定性护栏替代**。
  理由：三开关默认关闭 ⇒ **评测/CI 路径零行为变化**（不采 before 基线的前提成立）；交互面不属于评测口径（`SCORER_VERSION` 不动）。门槛：单测锁死「关闭态节点序列/事件序列与现状逐项一致」（条件边直连不产生额外节点）。
- **TBD-1（H1 澄清形态）—— 推荐：图内 `clarify` 节点 + interrupt**，拒绝两段式 API。
  理由：复用 R3 恢复语义（单一恢复机制、单一事实源）；澄清问答天然属于 run 的一部分（记账/审计/取消/配额复用）；两段式需复制准入/幂等/计费逻辑且队列模式下第二段无 run 归属。失败退化：LLM 判定失败 ⇒ 直接跳过澄清（零阻塞），记 `planner_events` 同构的策略事件（不进 `degradation_log`，不推 `degraded`）。
- **TBD-2（H1/H2 状态机与租约，核心）—— 推荐：显式两态 + 六处同步登记**（状态机 / `ACTIVE_STATUSES` / sweep / 取消 / 配额 / 等待时钟）。
  - 迁移 `0023_hitl_awaiting_states.sql`：放宽 `runs.status` CHECK（只增两值，不改旧值语义）；
  - `ACTIVE_STATUSES` 扩容两态（SSE 不断流、配额口径显式化）；
  - **并发/每日口径**：等待态**不占**并发闸（并发=同时执行）、**计入**每日次数；每用户等待数上限（推荐复用 `DR_MAX_USER_CONCURRENT` 值或新增 `DR_MAX_AWAITING_RUNS`，默认 2）；
  - **等待时钟**：暂停任务超时（`timeout_at` 顺延，等待时长不计任务预算）；`elapsed_seconds` 拆分「执行/等待」两段口径（TBD-7）；
  - **取消**：`request_cancel` 扩等待态 ⇒ 直接 `CANCELLED`（不进入 worker 节点边界路径）；
  - **唤醒**：用户回答 = API 事务内 `AWAITING_* → QUEUED` + Redis 信号（复用 `_start_queued` 同构路径）；worker 认领后 `resolve_execution_mode` 需识别「带回答的 resumed」。
- **TBD-3（H1/H2 等待策略）—— 推荐：不自动批准，TTL 兜底**。
  - 澄清/审批均等待，设 TTL（默认值待产品确认）；澄清超时可按空回答继续并留痕，**计划审批超时不自动批准**，v1 将 run 收为 `TIMED_OUT`（记录 `stop_reason=approval_expired`）并通知用户；如需超时自动继续，须在提交前明示并取得用户授权。
  - 澄清跳过、审批过期、用户主动取消分别记 `run_events`；避免把「等待超时」伪装成 worker 执行超时。
- **TBD-4（H3 追问接口）—— 推荐：`POST /api/research/{run_id}/follow-up`（同步 JSON，v1）**。
  - 前置校验：run 终局 + `has_report` + 输出 gate（flagged 阻断，同导出）+ 分享态不提供（同需求 26 边界）；
  - 上下文 = evidence manifest（有序清单 + 可取得模式）+ working findings 摘要 + 报告正文；**不触发检索/图**；
  - 输出契约：`{answer, citations:[evidence_id...], unverified_ids:[...]}`——回答中出现的 ID 不在 manifest ⇒ 零模型白名单校验标注（不阻断、如实呈现）；引用机制与报告引用链同源（可回溯）；
  - 记账：`kind=llm`（`role=follow_up`，`run_id` 关联）；**月预算是否纳入追问成本待拍板**（推荐纳入，防绕过月度闸）；
  - v1 **无跨轮对话记忆**（每次独立，上下文只含证据池）；审计事件 `FOLLOWUP_ASKED/FOLLOWUP_ANSWERED` 落 `run_events`（回答正文超限截断）。
- **TBD-5（开关与评测）—— 推荐：三独立开关 + 生产开、评测强制关**。
  `DR_HITL_CLARIFY`（默认开）/ `DR_HITL_PLAN_APPROVAL`（默认开，可配）/ `DR_HITL_FOLLOWUP`（默认开）；`eval/run.py` 与 `tests/conftest.py` 强制 false（延续 `DR_OTEL_ENABLED=false` 隔离模式）；`config_snapshot()` 增记录三开关值；**请求级不可绕过**（口径只在服务端）。
- **TBD-6（事件协议与前端）—— 推荐：AG-UI 扩展事件 + 持久化回放**。
  新事件 `CLARIFY_REQUESTED/CLARIFY_ANSWERED/PLAN_PROPOSED/PLAN_DECISION/FOLLOWUP_ANSWERED`（`agui.py` 扩展段，注释写明自定义理由）；全部进 `run_events`（SSE 重连后按 `sequence` 重建卡片状态）；发送交互卡后关闭本次 SSE 连接但不发送终态帧，客户端通过状态查询/重连获知等待态，避免连接长期占用；flagged 回放对 HITL 事件同样脱敏（用户回答可能含敏感文本）；前端复用 `Modal` 基础组件，testid 契约（如 `hitl-clarify-*`、`hitl-plan-*`、`follow-up-*`），e2e 覆盖两视口。
- **TBD-7（时间与成本口径）—— 推荐：elapsed 双段 + 全量记账**。
  澄清/审批修订/追问的 LLM 调用全部经 `LLMClient`（同准入/记账/时限口径）⇒ `usage_ledger` + `token_used`；报告 meta 与导出头新增「等待时长」字段（不混入执行耗时）。

## 6. 设计策略

- **复用而非另造**：恢复机制只有一套（R3 checkpoint + `resume`），HITL 扩展的是「resume 载荷」与「挂起出口」，不开第二条恢复路径；
- **关闭态零漂移**：关闭时条件边直连（无额外节点执行、无额外事件），单测锁死，保护 W8 基线与评测可比性；
- **等待态是一等公民**：状态机 / `ACTIVE_STATUSES` / sweep / 取消 / 配额 / 等待时钟 六处同步登记（登记点为单点常量，不散落字符串）；
- **审计即事件**：澄清问答、计划决策、追问全部落 `run_events`（可回放、可追溯），与「可归因/可回溯/可审计」主线一致；
- **追问不重搜**：证据池白名单校验 + 与导出同一输出 gate；宁可如实标注「回答含未在证据池中的 ID」，不冒充可回溯；
- **前端不越界**：`check_frontend_boundary.py` 守卫不触碰（前端只消费事件与调用接口）。

## 7. 验收标准（DoD）

> 交付状态（2026-10-13）：**P1~P3 全部交付**（PR #211 / #212 / #213，堆叠待合）。

- [x] TBD-0~7 拍板（记录进 §5）—— ✅（§12.6 开工登记，含 §12.4 五项决策处置）
- [x] H1：澄清节点 + `interrupt` + ≤2 轮；解答并入 `research_spec`（planner/critic/writer 同口径）；LLM 判定失败的跳过路径有单测 —— ✅（P2，#212；`test_hitl_graph.py`）
- [x] H2：计划审批 `interrupt`（批准/自然语言修改 ≤2 轮）；修订经同一 planner 口径；决策事件落 `run_events` —— ✅（P2，#212；修订失败保留原计划并留痕）
- [x] H3：`follow-up` 接口（前置校验齐全）+ 证据池 grounding + ID 白名单校验 + `usage_ledger` 记账 + 审计事件 —— ✅（P3，#213；`test_hitl_followup.py`）
- [x] 状态机：迁移 `0023`（只增）；状态机/`ACTIVE_STATUSES`/sweep/取消/配额/等待时钟六处同步，单测锁定（含「等待态不被 sweep 误杀」「等待态取消」「TTL 自动推进留痕」）—— ✅（P1，#211；`test_hitl_interaction_pg.py` 14 条）
- [x] 开关：三开关 + eval/CI 强制关 + `config_snapshot` 记录；**关闭态节点序列/事件序列与现状一致（确定性测试）** —— ✅（P2，#212；`test_hitl_graph.py` 拓扑硬断言 + `provenance.hitl_switches`）
- [x] 唤醒链：用户回答 → `QUEUED` → worker 认领 → 带回答 resume（真 PG 契约测试：挂起→回答→续跑，账本对账同 R3 口径）—— ✅（P1 回答事务 + P2 `Command(resume=)` + `test_hitl_resume_drill_pg.py` 真 PG 演练）
- [x] 前端：澄清卡/计划卡/追问框 + 断线重连可重建 + testid 契约 + e2e（桌面/窄屏）—— ✅（P3，#213；vitest 9 条 + 既有 e2e 80/80 不回归；**全链路 HITL e2e 待 staging 队列模式**，demo 模式按设计强制关闭）
- [x] 全量套件 0 失败；`SCORER_VERSION` 不变（w9.1），评测口径零漂移 —— ✅（P1~P3 各自本机全量绿；e2e 80/80）
- [x] 文档：协议入 `release-runbook.md`；需求 7 TBD-1d 状态更新（解冻落地）；roadmap 回填 —— ✅（§7.4 含追问；需求 7 TBD-1d 已加解冻登记；roadmap §3b P1~P3 已回填）

## 8. 影响范围与风险

- **动**：`research_engine/graph.py`（clarify 节点/条件边/挂起出口）、`state.py`（新字段 + `CHECKPOINT_SCHEMA_VERSION` 递增）、`agents/planner.py`（修订入口复用）；`web/backend/worker.py`（挂起路径 + resume 载荷）、`store.py`（状态/取消/清扫/计数）、`main.py`（回答接口/追问接口/校验）、`agui.py`、前端（新组件）；迁移 `0023`。
- 风险①：**状态机迁移**（改 CHECK 需只增；旧行不改）；`CHECKPOINT_SCHEMA_VERSION` 递增会令旧 checkpoint 判 `version_incompatible`（R3 已有显式 `replayed` 降级路径，影响面=灰度期的 in-flight 恢复，可接受）；
- 风险②：**等待态 × 租约/清扫交互**——漏登记任一处会出现「等待中的 run 被判 LOST / SSE 断流 / 配额口径错」；用六处同步 + 单测矩阵对冲；
- 风险③：**成本**——澄清/修订/追问都是 LLM 调用；轮次上限 + run 预算 + 月度闸三重约束；等待 TTL 防僵死；
- 风险④：**评测可比性**——关闭态必须零漂移；PR 前置项。
- 兜底：任一环节异常 ⇒ fail-open 回现状（澄清失败跳过、审批关闭直连、追问失败返回结构化错误不影响 run 终局状态）。

## 9. 测试策略

- 单测（离线）：澄清判定/轮次上限/跳过路径；审批批准与修订路由；挂起出口识别（`__interrupt__`）；状态迁移矩阵（含非法迁移拒绝）；关闭态节点序列不变（硬断言）；追问白名单校验；
- 真 PG 契约（CI `infra` lane）：挂起→回答→`QUEUED`→续跑（真 checkpoint + 真 saver，断言 `resumed` 语义与账本对账）；等待态清扫豁免；等待态取消；
- 事件/协议：SSE 帧序列 + 重连重建（`Last-Event-ID`）；flagged 回放脱敏；
- 前端：组件单测（vitest）+ e2e（澄清→审批→追问全链路，桌面/窄屏）；守卫（前端边界/testid 契约）；
- 全量套件 0 失败（干净 PG 容器 + 零 LLM/零网络）。

## 10. 排期

| 步骤 | 内容 | 依赖 | 粗估 |
|---|---|---|---|
| 0 | TBD-0~7 拍板 | — | 0.5 天 |
| H1 | 澄清（节点 + interrupt + 合并 spec + 跳过路径） | 拍板 + TBD-2 状态机 | 1.5~2 天 |
| H2 | 计划审批（挂起出口 + 修改回路 + 事件） | H1 基础设施 | 1.5~2 天 |
| H3 | 追问（接口 + grounding + 白名单 + 记账） | R2 产物（已有） | 1~1.5 天 |
| 收口 | 演练/文档/评测零漂移复核 | 全部 | 0.5~1 天 |

合计 ≈ **4~6 天**。⚠️ 调研粗估 3~5 天；拆解后上修，差异在：状态机迁移 + 五处同步、前端契约组件、真 PG 挂起→续跑契约测试（调研未计入）。

## 11. 变更记录

| 日期 | 类型 | 原因 | 改动摘要 | 关联 PR/commit |
|---|---|---|---|---|
| 2026-10-13 | 文档 | 批次 3 立项草稿（调研 §4） | 建稿：H1 澄清 / H2 计划审批 / H3 追问三单元拆解（含代码现状盘点 + 行号）；TBD-0~7（推荐 + 待拍板）；DoD/风险/测试/排期 | 本文档 |
| 2026-10-13 | 评审补充 | 运行协议与安全边界评审 | 明确挂起/恢复事务、租约释放、幂等回答、审批超时不自动批准、SSE 连接策略及 follow-up 的 claim-level grounding；建议分阶段演练 | 本文档 §12 |
| 2026-10-13 | 开工 | 主理人确认按需求 36 开工 | 状态草稿→进行中；Issue #210；开分支 `feat/36-hitl-interaction-protocol`；TBD-0~7 按 §5 推荐 + §12 修订执行（登记见 §12.6）；Phase 1（协议/状态机先行，不改图）开工 | #210 |
| 2026-10-13 | 实现 | Phase 1 协议/状态机交付（PR #211） | 迁移 `0023`（等待态两态 + 交互协议列 + 结构断言）+ `web/backend/hitl.py` 协议单点 + store PG 原子协议（`suspend_run`/`answer_interaction`/`expire_awaiting_runs`/`count_awaiting` + 取消扩展）+ `POST …/interaction` 回答 API + HITL 事件/回放脱敏/SSE 卡片后关流（TBD-6）+ 快照 `interaction`；离线 8/8 + 真 PG 契约 14/14 + 回归子集 70/70 + ruff 全过；**不改图** | #211 |
| 2026-10-13 | 实现 | Phase 2 图 interrupt + 挂起/恢复交付（PR #212，堆叠 #211） | 拓扑：clarify/await_clarify + await_plan/revise_plan（**关闭态零漂移**硬断言）；`Command(resume=)` 注入 + `STOP_SUSPENDED` 挂起出口（不算完成节点）；`Clarifier`（失败即跳过）+ `Planner.revise_plan`（失败留痕）+ `user_scope` 并入 `research_spec`；Worker 挂起事务（释放租约）/恢复携带回答值/清扫周期到期收口；`CHECKPOINT_SCHEMA_VERSION→2`；eval/CI 强制关 + `config_snapshot` 记录；真 PG「挂起-退出-回答-恢复」演练 + runbook §7.4；离线 23/23 + 真 PG 19/19 + 全量套件绿 | #212 |
| 2026-10-13 | 实现 | Phase 3 追问 + 前端交付（PR #213，堆叠 #212） | `web/backend/followup.py`（上下文上限/白名单校验/失败不改原 run）+ `POST …/follow-up`（开关/限流/门禁/记账/审计事件）+ `FOLLOWUP_*` 事件与回放脱敏 + options 下发开关；前端 `lib/hitl.ts`（事件流重建卡片/快照恢复）+ `useResearchStream`（interaction/answerInteraction/askFollowup/等待态关流）+ `HitlPanels`（澄清卡/计划卡/追问框）；离线 5/5 + 全量套件绿 + vitest 41/41 + e2e 80/80 + 守卫全过；**全链路 HITL e2e 待 staging**（demo 模式强制关） | #213 |
| 2026-10-13 | 实现 | CLI 端口径收口（并入 PR #213） | `cli.py` 入口强制关闭图上 HITL（clarify/plan_approval）并显式告警——与 API 非队列模式同口径（CLI 无「挂起-回答-恢复」通道；追问不受影响；`graph.run()` 的 interrupt 防御保留为兜底）；单测 3 条 + runbook §7.4 前置边界同步 | #213 |

## 12. 评审结论与补充设计

### 12.1 合理性结论

方向合理，且和已有 PG checkpoint、事件回放、evidence manifest 的投资连续；「澄清 / 批准 / 证据内追问」也应作为三个独立能力开关和独立验收单元。建议保留默认关闭及 `report` / 评测口径不变的硬约束。当前最需要收敛的是等待态的生命周期：LangGraph interrupt 只表示图已暂停，不会自动解决 DB 状态、worker 租约、SSE 终态、唤醒幂等和等待超时。

### 12.2 背景与必须固定的运行协议

- **背景**：任务库是队列模式的权威派发来源，Redis 只是可丢失的唤醒信号；worker 租约用于执行任务，不能覆盖用户等待时长。把 `AWAITING_*` 加入活动状态集合并不等于任务仍由 worker 持有。
- **挂起事务**：收到 LangGraph interrupt 后，worker 必须在一个可恢复边界内持久化 checkpoint、待答交互类型/轮次/摘要、事件序号和状态，再释放 lease；不能让 worker 线程或租约陪用户等 24 小时。状态、事件与交互 payload 应在同一 DB 事务提交，敏感原文按 flagged 规则脱敏，并设长度上限。
- **恢复事务**：回答接口带 `interaction_id` 与幂等键；单事务校验 run 所有权、当前状态、交互版本和轮次，写入回答事件、状态转 `QUEUED` 并保存 resume payload。重复提交返回既有结果；旧卡片/旧版本提交返回可解释冲突。Redis 唤醒可丢，worker 仍靠 DB 扫描认领。
- **等待态语义**：不持有 worker lease、不占执行并发槽；是否计入每日次数及等待态上限应作为配额政策单独测算。分别记录 `queued_at`、`started_at`、`awaiting_since`、`resumed_at`、`finished_at`，从权威时间戳计算执行、排队、等待时长，避免反复顺延 `timeout_at` 造成时钟漂移。
- **过期策略需改写**：计划审批 TTL 到期后自动按现计划继续，实质上等于自动批准，与「不自动批准」冲突。建议 v1 到期将 run 置为可恢复的 `TIMED_OUT`/明确的 `approval_expired` 终态并通知用户；若业务坚持自动继续，应单独配置、在提交前明示并留存用户授权。澄清超时可按空回答继续，但必须将其作为显式产品策略记录。
- **API 与 SSE**：交互卡片事件应包含稳定 ID、轮次、状态版本、过期时间和允许动作，不包含可执行任意命令的内容；SSE 只是通知/回放面，回答仍走经 CSRF/所有权校验的 API。重连从事件日志重建卡片，卡片提交不可依赖 SSE 当前连接。

### 12.3 H3 证据边界

Follow-up 的 ID 白名单只能验证「引用 ID 存在」，不能证明回答中的主张由证据支持。请求上下文应只包含 manifest 中可取得的证据摘要/受限原文窗口、报告中相关段落及明确的来源身份；不可取得窗口时必须告知模型并在响应中标注限制。输出需结构化为回答片段与引用 ID，服务端校验引用存在、来源可读取、引用数量及文本长度；未通过的主张应标为未核实或拒答，不能只把 ID 拼写错误标注后仍把整段当成已 grounded。对证据文本按不可信输入处理，隔离其提示注入指令。追问 LLM 调用沿用现有 usage sink 与月度预算；失败不得改变原 run 终态或报告。

### 12.4 实施顺序与准入

1. 先落事件/payload schema、状态迁移和 PG 原子协议，写挂起释放租约、重复回答、过期、取消、重连回放契约测试；此阶段不改图。
2. 在 eval 强制关闭下接入 plan interrupt 与 resume，完成真实 PG「挂起-进程退出-回答-另一 worker 恢复」演练后，再实现澄清回路。
3. 独立交付 follow-up API 和证据过滤/输出校验；桌面与窄屏 UI 最后接入同一交互事件协议。
4. 灰度先对内部用户启用单个开关，观察挂起数量、等待时长、过期率、恢复失败率和单位追问成本；确认清扫、告警、配额和删除策略完整后再扩大。

**立项前应补入 TBD 的决策**：审批 TTL 终态/是否自动继续；等待态生命周期与清理；回答幂等和并发提交冲突；H3 claim-level grounding 失败策略；旧 checkpoint 的版本迁移或 replay 对挂起 run 的影响。排期 4-6 天建议视为工程量下限，真实 PG 恢复协议与安全评审完成前不承诺交付日期。

### 12.5 同类实现与来源

- [LangGraph Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)：`interrupt()` 依赖 checkpointer/thread id，暂停后等待外部输入；恢复时节点会从头重新执行，因此 interrupt 前副作用必须幂等。官方还明确提醒不要在循环体中重复调用 interrupt，以免恢复时重放此前循环。适用于本需求的图内暂停机制；等待态 TTL、DB 事务、租约和用户授权仍需应用层负责。
- **可借鉴的优化**：同一个图节点一次只发出一个 JSON 可序列化交互 payload；将轮次和待问问题放在 checkpoint state，通过条件边控制最多两轮，避免把多轮交互写成含副作用的 interrupt 循环。PG 事务外的通知/唤醒采用 outbox 或可重试幂等动作。

### 12.6 开工登记（2026-10-13）

- **主理人口令**：按需求 36 开工（2026-10-13）；Issue **#210**；分支 `feat/36-hitl-interaction-protocol`。
- **TBD-0~7 执行口径**：按 §5 推荐 + §12 评审修订执行（TBD-3 = 审批超时不自动批准、v1 到期 `TIMED_OUT` + `stop_reason=approval_expired`；澄清超时按空回答继续并留痕——显式产品策略；TBD-6 = 发送交互卡后关闭本次 SSE 连接、不发送终态帧，重连从事件日志重建卡片）。
- **§12.4「立项前应补入」五项决策处置**：
  1. 审批 TTL 终态/是否自动继续 —— ✅ 由 §12.2 定案（不自动批准；`TIMED_OUT` + `approval_expired` 并通知）；
  2. 等待态生命周期与清理 —— ✅ 时间戳口径由 §12.2 定案（不持租约、不占并发槽；`awaiting_since`/`resumed_at` 权威时间戳，不反复顺延 `timeout_at`）；等待数上限数值随 Phase 2 实测校准（Phase 1 提供计数与常量）；
  3. 回答幂等和并发提交冲突 —— ✅ 由 §12.2 定案（`interaction_id` + 幂等键；重复提交返回既有结果；旧卡片/旧版本可解释冲突）；
  4. H3 claim-level grounding 失败策略 —— ✅ 由 §12.3 定案（未通过的主张标未核实或拒答；证据文本按不可信输入隔离）；
  5. 旧 checkpoint 版本迁移或 replay 对挂起 run 的影响 —— ⏳ **Phase 2 准入前置**（Phase 1 不动 state schema/`CHECKPOINT_SCHEMA_VERSION`；随 graph 改动单独拍板）。
- **实施顺序**：按 §12.4 分阶段——Phase 1（事件/payload schema + 状态迁移 + PG 原子协议 + 契约测试，**不改图**）→ Phase 2（plan interrupt + resume + 真 PG「挂起-进程退出-回答-另一 worker 恢复」演练，eval 强制关闭）→ Phase 3（follow-up API + 证据过滤/输出校验 → UI）。排期以 Phase 为单位独立验收。
