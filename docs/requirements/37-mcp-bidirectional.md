# 需求-37-mcp-bidirectional

> 状态流转：草稿 → 进行中 → 自测 → 待合 → 已合
> **性质**：**已立项（批次 4）**；开工登记见 §12.6（主理人 2026-10-14 确认按本需求开工；TBD-0~7 按 §5 推荐口径 + §12.4 六项按处置建议执行）。
> **来源**：`docs/extension-directions-survey.md` §3 方向 B / §4 批次 4（调研建议 P1）。
> **竞品参照**（调研 §1.1~§1.3）：GPT Researcher 对外提供 MCP Server；DeerFlow 接 MCP（私有域/知识图谱）；open_deep_research `MCPConfig` 运行时注入 researcher；Gemini 远程 MCP——四家全上，MCP 是 2026 的通用扩展口。
> **叙事（调研 §3 方向 B）**：与 WeChatBot（自研 MCP Server，Java/FastMCP）形成**跨生态呼应**——一边写 Server、一边消费 Server。
> **纪律**：① 默认关闭 + 评测/CI 零漂移；② **不破 `failure_reason` 单产者契约**（工具层五值封闭）；③ 依赖走 lock 外科式（先例：需求 10）；④ 外部终点登记进 egress 快照。
> 飞书镜像：https://wcnnpvbxd7li.feishu.cn/docx/IdoQd7RAloxz7mxwFA5cRTfknDh（`IdoQd7RAloxz7mxwFA5cRTfknDh`，位于 `DeepResearch 需求文档` 文件夹；2026-10-14 按开工版重建，立项版已删）。

## 1. 元信息
| 项 | 值 |
|---|---|
| 编号 | 37 |
| 标题 | 批次 4「MCP 双向」：消费外部 MCP 工具（注入 researcher）+ 对外 MCP Server（quick_search / deep_research / research_status / research_report） |
| 优先级 | P1 |
| 状态 | 进行中 |
| 负责人 | TianJinYing2006 |
| 关联 Issue | #218 |
| 关联 PR | 待定 |
| 创建 / 更新 | 2026-10-13 / 2026-10-14 |
| 前置评审 | **TBD-0~13 已拍板**（2026-10-14 按推荐；记录见 §5 / §12.6） |

## 2. 问题背景

- **方向判断（调研 §0/§1.3/§2）**：数据源扩展（MCP 双向）与交互闭环并列为两大缺口（🔴）。MCP 是四家竞品共同选择的扩展口：消费侧让 researcher 接外部世界（私有域/知识图谱/垂直检索），暴露侧让本项目成为别家 Agent 的工具（跨生态）。
- **代码现状（实测事实，全部为「不存在」或「硬编码」，MCP 落地的真实起点）**：
  - 全仓**无 MCP 代码、无 MCP 依赖、无 MCP ADR**（唯一字面命中是落地页文案与冻结评测结果里的无关文本）。
  - **没有工具框架**：`research_engine/tools/` 仅 `code_exec.py` 与空 `__init__.py`；工具选择是硬编码字典 `targets = {"web", "rag", "arxiv", "code"}`（`research_engine/agents/researcher.py:367-373`），逐跳并行分发（`:376-381`）——**无注册表、无 JSON schema、无 OpenAI function calling**（`llm/client.py:117` 只读 `message.content`）。
  - **失败分类法封闭**：工具层五值 `not_configured/timeout/provider_error/empty_result/parse_error` 由单产者契约锁定（`research_engine/failure_reasons.py:42-68`、`:16-24`）；`empty_result` 不算故障（`:113`）。
  - **记账账本封闭**：`usage_ledger.kind CHECK IN ('llm','embedding','search')`（`migrations/0011_usage_ledger.sql:18`）；非 LLM 计费 `per_call/0 元`（`web/backend/usage.py:33-37`）。
  - **鉴权只有浏览器态**：会话 Cookie + CSRF（`main.py:696-747`）；机器凭据先例仅 `DR_OPS_TOKEN`（`:304-306`、常数时间比较 `:729-733`）；**无 API key 表**。
  - **无 quick_search 端点**：最薄可复用单元是 provider 级 `create_search_provider(name).search(query)`（`research_engine/search/base.py:66-80`，失败结构化）。
  - **评测/CI 零 LLM、零网络**（`.github/workflows/ci.yml:3-4`；eval 手动触发 `eval-manual.yml`）；行为开关必须进 `config_snapshot()`（`eval/provenance.py:118-144`）。
  - **外部终点披露**已有单点：run 级 egress 快照（`web/backend/egress.py:27-57`）。
  - 关联挂账：roadmap §7 **12.7「外部调用统一准入与货币账本」**——本批消费/暴露两侧的准入与记账须与该口径对齐，不另造。

## 3. 需求分析

目标（一句话）：**让 researcher 能消费外部 MCP 工具、让本项目能以 MCP Server 形态对外提供研究能力；两侧共用既有准入/配额/审计口径，默认关闭、可归因。**

- **M1 消费外部 MCP（注入 researcher）**：配置若干远程 MCP Server（检索型工具）；研究每跳并行工具集新增 `mcp` 位；结果归一化为 `ResearchFinding`（`source_type='mcp'`，携带 server 身份）；失败映射到既有五值；成功/失败均记账；外部终点进 egress 快照；默认关闭。
- **M2 对外 MCP Server**：暴露工具面 `quick_search`（provider 直调，同步）/ `deep_research`（创建 run，异步返回 `run_id`）/ `research_status`（状态查询）/ `research_report`（md/json 取回）；复用**同一准入/配额/内容安全**口径（不允许第二条创建链路）；机器鉴权；操作全审计。
- **边界（v1 不做）**：stdio 传输（容器内子进程管理 + 本地边界风险）；LLM function-calling 式工具自选（与「无工具注册表」架构一致性，见 TBD-3）；用户级 MCP Server 配置（全局运营配置）；MCP 结果逐字引文/快照（无网页快照语义，manifest 标注 retrieval 模式）；对外开放取消/删除等写操作。

## 4. 当前设计（代码现状）

| 层 | 位置 | 现状 | 本批次处置 |
|---|---|---|---|
| 工具分发 | `researcher.py:361-396` | 硬编码 `targets` 字典；逐跳并行；单工具异常不绊倒整跳 | **M1**：新增 `mcp` 位（沿用 `copy_context()` 记账传递） |
| 工具池/统计/展示 | `researcher.py:448/470/485-509` | `_TOOL_KEY_BY_SOURCE_TYPE`/`text_tools`/`format_tool_detail` 三处硬编码元组 | **M1**：三处同步注册（防半接线） |
| 失败分类 | `failure_reasons.py:42-68` | 五值单产者契约 | **M1**：MCP 异常映射现有值，不新增枚举 |
| 记账 | `migrations/0011:18`、`usage.py:43-93`、`web/backend/usage.py:29-57` | kind 三值封闭；非 LLM per_call/0 元 | **M1**：新增 `kind='tool'`（迁移顺延）或复用既有 kind（TBD-4） |
| 检索复用 | `search/base.py:66-80` | provider 直调可用（bocha/tavily/arxiv） | **M2**：`quick_search` 直接复用 |
| 创建链路 | `main.py:1347-1427`、`store.py:271-418` | 准入+创建原子化（`create_run_admitted`）；配额三闸 | **M2**：抽服务函数，API/MCP 单一准入口径 |
| 鉴权 | `main.py:304-306, 722-735` | Cookie/CSRF 浏览器态；ops token 先例 | **M2**：机器令牌（TBD-5） |
| egress | `egress.py:27-57` | run 级数据流向快照 | **M1**：MCP 终点登记 |
| 评测 | `provenance.py:118-144`、`ci.yml:3-4` | 开关进 config_snapshot；CI 零网络 | **M1/M2**：开关入 provenance；fake server 测试 |
| SSRF/出网 | `net/safe_fetch.py:72-134` | 声明为 URL 抓取唯一入口；DNS 全解析校验 | **M1**：MCP URL 校验同源复用 |

## 5. 优化方案（TBD 推荐 + 拍板记录）

- **TBD-0（前置，P0）before 基线 —— 推荐：不采，以「默认关闭 + 关闭态零漂移」护栏替代**。
  理由：MCP 两侧默认关闭 ⇒ 主链路（含评测路径）零行为变化；开关进 `config_snapshot`，历史可比性不受影响。
- **TBD-1（SDK 与传输选型）—— 推荐：官方 `mcp` Python SDK；v1 仅 Streamable HTTP**。
  消费与暴露两侧同协议族；stdio 不做（容器内子进程 + 本地网络边界风险，见 TBD-2）；SDK 入 lock（外科式）。
- **TBD-2（配置与安全边界）—— 推荐：全局配置 + 三重边界**。
  `DR_MCP_SERVERS`（JSON：name / url / 工具白名单 / 超时（默认 ≤15s）/ 结果上限）；默认空 = 关闭；URL 校验复用 `safe_fetch` 同源逻辑（scheme/TLS/禁本机与内网段，需显式放行才允许私有段）；外部终点登记 egress；不做用户级配置（数据外发方向必须运营可控）。
- **TBD-3（消费调用契约）—— 推荐：每跳并入 `mcp` 工具位，只支持「查询型工具」**。
  v1 约定：每个 server 显式配置检索型工具名 + query 注入方式；**不做 LLM function-calling 工具自选**——本项目工具面是确定性分发（`researcher.py:367-373`），单为 MCP 引入第二套工具选择机制会破坏「工具层是失败五值唯一产生点」的可归因结构；通用 tool-calling 作为独立议题后置。
- **TBD-4（失败映射 / 记账 / 来源标签）—— 推荐：映射既有五值 + 新 `kind='tool'` + `source_type='mcp'`**。
  映射：连接失败/鉴权失败 ⇒ `provider_error`；超时 ⇒ `timeout`；响应解析失败 ⇒ `parse_error`；零结果 ⇒ `empty_result`；未配置 ⇒ `not_configured`（**零新增枚举**，契约测试 `test_arm4_failure_reasons.py` 不破）；`DegradationEntry.component` 注释扩 `'mcp'`（`state.py:39`）；记账新增 `usage_ledger.kind='tool'`（迁移顺延，`per_call` 计次；严格记账失败上抛，同 F06 语义）；渲染来源标签扩展（`render.py:70-79` 同构新增，如 `🧩mcp:<server>`）。
- **TBD-5（对外鉴权）—— 推荐：v1 单机器令牌仅用于封闭内测**。
  内测可用 `DR_MCP_TOKEN`（常数时间比较，`DR_OPS_TOKEN` 同构先例），绑定一个独立 service principal 和固定数据归属 + 审计标识（`actor=mcp:<name>`）；不得查询其他用户 run。面向多个调用方前需补可撤销凭证、scope、轮换及主体映射；所有 MCP 工具调用过同一限流器与审计。
- **TBD-6（对外工具面与准入单一口径）—— 推荐：四工具 + 服务函数抽取**。
  `quick_search`（同步；provider 直调；记账 `kind='search'`）；`deep_research`（**从 `POST /api/research` 抽 `start_research(...)` 服务函数**，API 与 MCP 共用：准入/配额/内容审核/注入扫描/幂等一处不落）→ 返回 `run_id`；`research_status`；`research_report`（复用导出，输出 gate/flagged 阻断同一口径）。异步语义明确：`deep_research` 立即返回 run_id，配套 status/report 轮询。
- **TBD-7（评测 / CI / 开关）—— 推荐：默认关 + 全链路可关**。
  `DR_MCP_CONSUME_ENABLED` / `DR_MCP_SERVER_ENABLED`（均默认 false）；`config_snapshot` 记录开关与 server 列表指纹（不含密钥）；eval 强制关；CI 用 fake MCP server（零网络）覆盖消费映射与对外准入。
- **TBD-8（M1/M2 拆分发布；§12.4 补入）—— 拍板：拆分，M1（消费）先行、M2（对外）独立发布窗口**。
  §12.1 结论：两侧安全边界/部署形态/验收目标不同，不被一个开关或一个发布窗口绑死。
- **TBD-9（首发 server 与可信运营方；§12.4 补入）—— 拍板：仅运营方自建/可信内部 server（如既有 WeChatBot 自研 Server），单 server 灰度**。
  不接第三方公共 server；具体 server 由运营方指定并登记 egress。
- **TBD-10（service principal 主体归属；§12.4 补入）—— 拍板：单一独立主体 `mcp:<name>`，固定数据归属**。
  所有 run 归属该主体、仅该主体可查（`run_id` 不构成越权通道）；跨主体开放前补可撤销凭证/scope/轮换。
- **TBD-11（私网例外审批方式；§12.4 补入）—— 拍板：按 server 显式配置 + 理由 + 审计**。
  不接受普通 URL 字符串自行覆盖；生产环境另以出口策略限制 worker 可达网段。
- **TBD-12（响应正文保存/删除周期；§12.4 补入）—— 拍板：不额外落盘**。
  审计/账单仅记元数据（server/tool/耗时/字节数/条目数/失败类别，不记完整正文）；findings 随 run 既有生命周期与截断/内容 gate；排障采样走既有日志口径。
- **TBD-13（工具调用预算结算；§12.4 补入）—— 拍板：按调用次数 `per_call` 计次**。
  金额仅当服务端提供可信价格才写；不按供应商成本结算（避免把 `per_call/0 元` 误读为免费）。
- **拍板记录（2026-10-14）**：TBD-0~13 全部按上述推荐口径拍板（主理人确认；开工登记见 §12.6）。

## 6. 设计策略

- **既有契约不破**：失败五值、`empty_result` 非故障、单产者契约、严格记账——MCP 是「接入方」不是「新特权层」；
- **准入单一口径**：对外创建 run 只能走与 Web 相同的服务函数（配额/审核/幂等一处定义），MCP 不得绕过；
- **默认关闭**：两侧都关 = 现状；打开 MCP 是一次显式的运营决策（含数据外发披露）；
- **可归因优先于功能多**：MCP 调用计次入账、终点入 egress、结果带 server 身份——回答「谁收到了什么、花了多少」；
- **依赖纪律**：SDK 加锁走外科式（先例：需求 10「lock 外科式加锁，零版本漂移」），lock 重生成单列变更。

## 7. 验收标准（DoD）

- [x] TBD-0~13 拍板（记录进 §5 / §12.6）—— ✅（2026-10-14，按推荐）
- [ ] M1：`mcp` 工具位（查询型）+ 三处注册（池/统计/展示同步）+ 结果归一化 finding（含 server 身份）
- [ ] M1：失败映射五值（契约测试不破）+ `component='mcp'` + `kind='tool'` 记账（迁移 + 真 PG 断言）
- [ ] M1：URL 安全校验（禁本机/内网默认）+ egress 终点登记 + 超时/结果上限
- [ ] M2：四工具（quick_search / deep_research / research_status / research_report）+ 机器令牌鉴权 + 审计
- [ ] M2：`start_research` 服务函数抽取；API 与 MCP **同一准入口径**（配额/审核/幂等测试证明无旁路）
- [ ] 开关：两开关默认关；`config_snapshot` 记录；eval 强制关；CI 零网络（fake server）
- [ ] 关闭态零漂移（主链路与评测路径行为逐项一致，确定性测试）
- [ ] 全量套件 0 失败；`SCORER_VERSION` 不变；lock 变更单列且 `pip check` 通过
- [ ] 文档：MCP 工具面协议进 `release-runbook.md`；egress/合规口径回填（production-readiness）

## 8. 影响范围与风险

- **动**：`research_engine/agents/researcher.py`（mcp 位）、`failure_reasons.py`/`state.py`（注释/常量）、`research_engine/usage.py` + `web/backend/usage.py`（tool kind）、`net` 校验复用；`web/backend/main.py`（服务函数抽取 + MCP 路由/进程挂载）、`store.py`（只读查询复用）；迁移（tool kind，编号顺延）；`requirements*`（mcp SDK）；新增 `web/backend/mcp_server.py`（或等价模块）。
- 风险①：**外部数据不可控**（MCP 返回内容进 findings）⇒ 沿用既有下限（`strip_invisible` + 截断 + 内容安全 gate + validator 引用校验）；数据外发须运营披露（egress）；
- 风险②：**准入旁路**（MCP 若自建创建链路会造成配额/审核真空）⇒ 服务函数单一入口 + 测试锁死；
- 风险③：**SSRF/内网**（MCP URL 由运营配置但仍校验）⇒ 复用 `safe_fetch` 校验语义；
- 风险④：**协议漂移**（MCP 规范演进快）⇒ SDK 锁版本 + 适配层隔离（协议变化只改适配层）；
- 兜底：连接/调用失败按失败分类降级（不阻断整跳，同工具隔离语义）；关闭开关即完全回现状。

## 9. 测试策略

- 单测（离线，fake MCP server/stub client）：调用成功归一化、失败五值映射、超时、结果上限、三处注册一致性、关闭开关时 `targets` 与现状一致（硬断言）；
- 记账：`kind='tool'` 写入与对账（`usage_ledger` 真 PG 契约）；严格模式上抛；
- 对外：鉴权（错 token 拒绝/常数时间）、四工具语义、**准入同口径**（配额熔断/幂等/审核阻断在 MCP 路径同样生效）、审计事件；
- 安全：URL 校验矩阵（scheme/TLS/本机/内网/显式放行）；egress 登记断言；
- 评测：开关进 `config_snapshot`；eval 强制关断言；
- 全量套件 0 失败（零 LLM/零网络）。

## 10. 排期

| 步骤 | 内容 | 依赖 | 粗估 |
|---|---|---|---|
| 0 | TBD-0~13 拍板 | — | ✅（2026-10-14） |
| 0' | SDK 入 lock（外科式）+ 适配层骨架 | TBD-1 | 0.5 天 |
| M1 | 消费外部 MCP（工具位 + 映射 + 记账 + 安全 + egress） | 拍板 | 2~3 天 |
| M2 | 对外 Server（鉴权 + 四工具 + 服务函数抽取 + 审计） | 拍板 | 2~3 天 |
| 收口 | 全链路测试 + 文档 + 关闭态零漂移复核 | 全部 | 0.5~1 天 |

合计 ≈ **5~7 天**（与调研 4~6 天同量级；上沿差异在服务函数抽取与准入同口径测试）。

## 11. 变更记录

| 日期 | 类型 | 原因 | 改动摘要 | 关联 PR/commit |
|---|---|---|---|---|
| 2026-10-13 | 文档 | 批次 4 立项草稿（调研 §4） | 建稿：M1 消费 / M2 对外两单元拆解（含代码现状盘点 + 行号）；TBD-0~7（推荐 + 待拍板）；DoD/风险/测试/排期 | 本文档 |
| 2026-10-13 | 评审补充 | 安全与发布边界评审 | 补充 MCP 消费出网/提示注入治理、服务端主体归属与准入幂等；建议 M1/M2 分阶段发布，单 token 限于封闭内测 | 本文档 §12 |
| 2026-10-13 | 文档 | 飞书镜像建立 | 镜像至 `DeepResearch 需求文档` 文件夹（`S4UodQCmRophHmx5KCMcIwCznYb`；自引用已回填） | — |
| 2026-10-14 | 开工 | 主理人确认按需求 37 开工 | 状态草稿→进行中；Issue #218；TBD-0~7 按 §5 推荐 + §12.4 六项处置建议拍板（TBD-8~13 补入 §5；登记见 §12.6）；M1（消费）先行开工 | #218 |
| 2026-10-14 | 文档 | 飞书镜像重建 | 按开工版重建至 `DeepResearch 需求文档` 文件夹（`IdoQd7RAloxz7mxwFA5cRTfknDh`；自引用回填；立项版已删） | — |

## 12. 评审结论与补充设计

### 12.1 合理性结论

双向 MCP 的方向合理，但消费端与服务端是两种不同的安全边界、部署形态和验收目标。建议立项时分成 M1（消费）与 M2（对外）两个可独立发布的阶段；M2 依赖 M1 的 SDK 不是理由，两个方向可以复用协议适配层但不应被一个开关或一个发布窗口绑死。默认关闭、五值 failure reason 不扩、Web 创建链路单一准入、egress 可审计均应保留。

### 12.2 背景与消费端协议

- **背景**：当前 researcher 是确定性 fan-out，并无通用工具选择器；MCP 首版更适合作为显式配置的只读检索源，而非授予模型任意工具调用权。远程工具返回值属于不可信外部内容，会进入研究上下文，因此需要与网页/RAG 一样的归一化、长度限制和注入防御。
- 配置应有版本化 schema：server 唯一名称、URL、允许工具名、固定参数映射、连接/调用超时、最大响应字节数、最大 finding 数、密钥引用名、私网例外及其理由。配置错误应在启动时 fail-fast；配置指纹进入 provenance，但不得写入 token、header 或 URL 凭据。
- 对每个 server 建立独立客户端/连接池与并发信号量；调用超时、连接错误、协议错误分别归入现有五值。响应解析之后再执行文本清洗、截断、内容安全和 prompt-injection 防御；finding 保留 `server_id/tool_name/call_id/retrieval_time`，manifest 诚实标为 MCP retrieval，不伪装成网页快照或逐字引文。
- `safe_fetch` 的 URL 校验语义可复用，但 MCP SDK 自己发起的连接必须确保同样的 DNS 解析/IP 拒绝策略、重定向逐跳重验、TLS 校验和连接目标绑定，防 DNS rebinding。默认只准 HTTPS；私网放行应是受审计的按 server 配置，不接受普通 URL 字符串自行覆盖。生产环境还应通过网络出口策略限制 worker 可达网段。
- 工具调用的账单事件至少记录 server/tool、耗时、结果字节/条目数和失败类别；不记录完整响应正文。工具计次与货币成本是不同维度，只有服务端能提供可信价格时才写金额，避免把 `per_call/0 元` 误读为免费成本。

### 12.3 对外 Server 身份与准入

- 单一共享 `DR_MCP_TOKEN` 适合封闭内网试点，不适合作为面向多个调用方的长期鉴权模型。v1 若用单 token，必须明确它是一个独立 service principal，所有 run 归属此主体且只能由该主体查询；不得凭 `run_id` 让 token 读取任意用户报告。跨主体开放前需要可撤销凭证、scope、轮换和主体到用户/租户的映射。
- MCP 路由只启用 Streamable HTTP，强制 TLS、请求体/响应体上限、限速、并发上限和来源日志脱敏；拒绝 query-string token。工具 schema 需固定且版本化，`deep_research` 仅返回 `{run_id,status_url}`，后续 status/report 每次都做主体所有权校验。report 仍复用输出 gate，flagged/under-review 不可绕过。
- 把创建链路抽为服务函数时，定义事务和失败语义：幂等键由调用方提供或按主体+请求哈希生成；准入、内容审核、注入检查、配额预留及创建要沿用现有原子边界。MCP 不直接拼接另一个请求去调用 HTTP API，也不复用浏览器 Cookie/CSRF 作为机器身份。
- `quick_search` 必须限定 provider、query 长度、结果条数和可返回字段；不能将所有 provider 凭据或任意 URL 暴露给调用方。审计记录主体、工具、run_id、请求哈希和决策结果，不落原始敏感 prompt。

### 12.4 实施策略

1. M1 先实现独立 MCP adapter 与 fake server 契约测试，验证发现/调用/超时/协议错误/超限/注入防御；然后以单个内部 server 灰度，确认 egress 和账单对账。
2. M2 先抽取并测试 `start_research` 服务边界，确保 Web 行为不变；再接入 MCP transport、service principal 和只读 status/report；最后开放 `deep_research`，按调用方限额观察。
3. SDK 版本、传输版本、配置 schema 和工具 schema 一起锁定。CI fake server 仅覆盖协议契约；真实部署另做 TLS、DNS/IP、重定向及出口网络验证，零网络 CI 不能替代这些验证。

**立项前应补入 TBD 的决策**：M1/M2 是否拆分发布；首发 server 类型与可信运营方；service principal 的主体归属；私网例外审批方式；响应正文保存/删除周期；工具调用预算是否按调用次数还是供应商成本结算。5-7 天估算未包含多租户凭证、出口策略改造和外部协议互操作排障，应按受控内测范围估时。

### 12.5 同类实现与来源

- [MCP Authorization Specification, 2025-11-25](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization)：HTTP transport 的授权可选；实现授权时，规范建议遵循基于 OAuth 2.1 的发现与授权流程，并要求受保护资源元数据发现、token audience 校验等。结论不是 v1 必须自建完整 OAuth，而是共享 bearer token 仅适合明确封闭的内测，不能宣传为通用 MCP 身份方案。
- [MCP Server Tools Specification, 2025-11-25](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)：工具调用结果可以是结构化或非结构化内容，执行错误通过工具结果返回。建议服务端对外保持稳定、有限的工具 schema，并将应用级配额/授权/所有权校验作为真正的安全边界，而非依赖客户端 UI 或工具描述。
- [GPT Researcher MCP Server](https://github.com/assafelovic/gptr-mcp)：README 将 `deep_research`、`quick_search`、`write_report`、`get_research_sources` 和 `get_research_context` 分开。可借鉴的是“研究/报告/来源”职责分离；本项目宜补一个分页、受权限约束的 `research_sources`，而不直接暴露可能很大的完整 `research_context`。该项目支持多种 transport；本项目限定 Streamable HTTP 是部署与安全边界选择，不是 MCP 的唯一做法。

### 12.6 开工登记（2026-10-14）

- **主理人口令**：按需求 37 开工（2026-10-14）；Issue **#218**；分支 `feat/37-mcp-consume`（M1 先行）。
- **TBD-0~13 执行口径**：TBD-0~7 按 §5 推荐口径；§12.4 六项按处置建议补入 §5（TBD-8~13）：① M1/M2 拆分发布（M1 消费先行、M2 独立窗口）；② 首发 server 限运营方自建/可信内部（单 server 灰度，不接第三方公共）；③ service principal 单一主体 `mcp:<name>`，固定数据归属、仅查自己 run；④ 私网例外按 server 显式配置 + 理由 + 审计，不接受 URL 自覆盖；⑤ 响应正文不额外落盘（审计/账单仅元数据），findings 随 run 既有生命周期；⑥ 预算按调用次数 `per_call` 计次，金额仅服务端有可信价格时写。
- **§12.4「立项前应补入」六项决策处置**：① M1/M2 拆分 —— ✅（TBD-8）；② 首发 server/运营方 —— ✅（TBD-9）；③ principal 归属 —— ✅（TBD-10）；④ 私网例外审批 —— ✅（TBD-11）；⑤ 响应正文留存 —— ✅（TBD-12）；⑥ 预算结算 —— ✅（TBD-13）。
- **实施顺序**：M1（SDK lock 外科式 + 适配层骨架 → `mcp` 工具位/三处注册 + 五值映射 + `kind='tool'` 记账 + URL 安全边界 + egress）→ M2（机器令牌 + 四工具 + `start_research` 服务函数抽取 + 审计）→ 收口（全链路 + 文档 + 关闭态零漂移复核）。排期 ≈5~7 天（受控内测口径；不含多租户凭证/出口策略改造/外部互操作排障）。
