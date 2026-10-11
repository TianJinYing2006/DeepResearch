# 需求-33-otel-observability-migration

> 状态流转：草稿 → 进行中 → 自测 → 待合 → 已合
> **反转记录（重要）**：本文档反转需求 3（W3 Langfuse）在
> `docs/requirements/3-langfuse-observability.md:53` 的既有拍板「OTel OTLP 出局」
> ⇒ 需新增 `docs/decisions/0012-*.md` 正式记录反转理由与代价，并在需求 3 变更记录追加注记。
> **排期**：本文档承载「本批次 OTel 迁移排期」（§10）与「同期批次排期：对照 EvidenceFlow 的
> 借鉴项」（§11，独立立项）两部分。
> **飞书镜像**：**已创建（2026-10-11）** —— docx token `YHiEdRhNpoqRs4xUMyxcIFBHnXX`｜
> 显示名「需求设计文档：OTel 观测迁移」｜
> URL https://wcnnpvbxd7li.feishu.cn/docx/YHiEdRhNpoqRs4xUMyxcIFBHnXX
> （经导入接口创建；正文文档在个人空间，待客户端移入 `DeepResearch 需求文档` 文件夹
> `MYR6fazL5la0ardJdUecOBkVnd8`）。

## 1. 元信息
| 项 | 值 |
|---|---|
| 编号 | 28 |
| 标题 | OTel 观测迁移（Langfuse → OTel 原生，对齐国内技术栈叙事） |
| 优先级 | P1 |
| 状态 | 已合（PR #181 → dev `7355ba4`；真实冒烟见 §9.1） |
| 负责人 | TianJinYing2006 |
| 关联 Issue | #180 |
| 关联 PR | — |
| 创建 / 更新 | 2026-10-11 |
| 前置评审 | §5 TBD-1~8 待设计评审逐项拍板 |

## 2. 问题背景

- **2026-10-11 主理人拍板方向**：可观测层从 Langfuse 迁移为 **OTel 原生**——贴合国内技术圈侧重
  （企业 APM 与云厂商几乎全以 OTLP 为入口，GenAI 语义约定由 OTel 社区推动；Langfuse 国内认知度低、
  Cloud 为境外服务）。
- **现状是「双层」而非「没有 OTel」**：
  - Web 侧已是 OTel（P1-8）：`web/backend/otel.py`（OTLP + Prometheus 双路径、FastAPI/httpx 自动埋点、
    `gen_ai.client.token.usage` input/output 拆分、resource 属性、默认全关）；
  - 引擎侧是 Langfuse SDK（W3）：`research_engine/observability.py`（7 节点 span + generation
    自动 usage/cost + trace URL 回显）。
- **需求 3 当时明确否决过「OTel 替代 Langfuse」**（`3-langfuse-observability.md:53` 原文：
  「OTel OTLP 出局（Python 侧样板过重；WeChatBot 走 OTel 系 Java SDK 弃用所致，Python 无此限制）」）。
  本文档按纪律**如实记录反转**，并接受 W3 分析曾指出的代价。
- **反转理由**：单栈统一（API / Worker / 引擎全 OTLP）、国内面试与开源叙事、去除境外服务默认依赖、
  消除 `langfuse.openai` 全局 monkey-patch 与随之而来的 openai 版本锁
  （`requirements.txt:5` 的 `openai>=1.50,<1.93` 专为包装器适配窗口而锁）。
- **反转代价（接受清单）**：W3 资产重做（`tools/verify_w3_trace.py`、冒烟记录、演示截图、README 口径）；
  trace URL 需自建（后端 URL 模板）；Langfuse 控制台 cost 列消失（本地成本行本就自算，影响小）。
- **W3 已验证的结构性资产全部平移，不重做**：埋点锚点（`LLMClient.chat` 全项目唯一 LLM 出口）、
  `tokens_total` 对账（`llm/client.py:164`）、定价表（`config.llm.pricing`）、扁平轮次 span 设计
  （Q2=C1'）、fail-silent 纪律、脱敏截断常量——只换承载物（SDK → OTel），不重开已定设计。
- **顺带收益**：`web/backend/egress.py:53` 的 `langfuse_configured` 披露项按新后端改写后，L3 默认关
  的「境外服务」语义随之简化（后端可自托管）。

## 3. 需求分析

目标（要具体解决什么）：
1. `research_engine` 观测层**零 Langfuse 依赖**，改为 OTel 原生（属性遵循 GenAI 语义约定）；
2. **后端可插拔**：本地 Jaeger（零外部依赖演示）/ 自托管 Langfuse（OTLP ingest，保留 LLM 专属视图）/
   任意 OTLP 后端；与 Web 侧 `otel.py` 同一导出链路与 env；
3. **能力不许退化**：三态开关 + 知情打印、fail-silent、脱敏截断常量、thread 关联与同 thread 历史、
   token 对账差、CLI 成本行（人民币）、trace URL 回显；
4. **默认关闭零行为变化**（测试与本地不受影响，延续既有纪律）。

成功定义（可量化）：
- 一次 research 在本地 Jaeger（或自托管 Langfuse）可见完整 span 树：根 span + 7 节点 + LLM generation
  （含 usage 属性），多轮 critic 循环的轮次前缀可辨；
- CLI 回显 trace URL + 成本行；对账差（`tokens_total - state.token_used`）与现状口径一致；
- 未配置 OTLP 端点时：pytest 全绿、零外发、零行为变化；
- README / 简历 / 演示资产完成口径切换（落地才写）。

## 4. 当前设计（代码现状）

| 层 | 位置 | 现状 | 迁移处置 |
|---|---|---|---|
| Web 基础设施 | `web/backend/otel.py` | OTLP + Prometheus、GenAI 指标、FastAPI/httpx 埋点、默认关；`main.py:97-99`、`worker.py:654` 接线 | **保留**，作为统一导出底座（引擎复用同一 env） |
| 引擎 trace/span | `research_engine/observability.py`（213 行） | Langfuse SDK：`get_langfuse` 惰性单例、`create_trace_id`（32-hex）、`start_trace`、`span_node`、`flush_and_get_url`、`format_cost_report`、`mask_callback`、`status_line` | **重写为 OTel 原生**，函数名与语义尽量保留（调用点改动最小） |
| 引擎接线 | `graph.py:519-529`（`run`）、`:622-632`（`iter_run`）；`cli.py:14-16,29,39,73` | trace 包裹 invoke；`self.trace_id` 自持供 finally 回显 | 接线微调；URL 模板换后端 |
| LLM 单点 | `llm/client.py:59`（`chat`）、`:141/:164`（无条件累计）、`:184`（reset） | `tokens_total` 对账（W3 Q3=D'） | generation span 在 `chat` 单点埋；对账逻辑不动 |
| 配置 | `config.py:165-178` `LangfuseConfig`（7 字段） | 三态开关 / host / sample_rate / mask_sensitive | 替换或并入 OTel 配置（旧字段兼容策略见 TBD-8） |
| 数据去向披露 | `web/backend/egress.py:30-31,53` | `langfuse_configured` 写入 run 的 egress 快照 | 按新后端改写披露项 |
| 依赖 | `requirements.txt:5,41,48-53` | langfuse 锁 + openai 1.x 锁（为包装器）；OTel 六件套已在（Web 用） | 移除 langfuse；openai 锁复评（TBD-8）；OTel 依赖不动 |
| 资产 | `tools/verify_w3_trace.py`、`tests/test_observability.py`、`README.md:21/89/390` | Langfuse 对账 / 单测 / 叙事 | 重写 / 更新（见 DoD） |

痛点：单栈分裂（引擎 Langfuse SDK vs Web OTel）；默认依赖境外后端；monkey-patch 风险面
（W3.2 已因版本错配付出一次修复成本）；国内生态（面试官 / 企业栈）不识别 Langfuse。

## 5. 优化方案（草案，TBD 待设计评审）

**目标形态**：

```
engine OTel tracer ──OTLP──▶ OTLP 后端（Jaeger | 自托管 Langfuse | ...）
        ▲ 与 web/backend/otel.py 同 env：OTEL_EXPORTER_OTLP_ENDPOINT / DR_* 开关
```

**观测点平移映射（Langfuse → OTel）**：

| 现状（W3） | OTel 对应 |
|---|---|
| 根 trace `deepresearch: <topic>` | root span（attrs：topic 截断 / thread_id / model） |
| 节点 span（中文名 + `metadata.node`、`R{k}-` 前缀） | span name 同；attrs `dr.node` / `dr.round` / `dr.depth` / `dr.sq_id` |
| generation（langfuse.openai 自动 usage/cost） | `LLMClient.chat` 单点 span（`gen_ai.*`：provider / request.model / operation / usage input/output） |
| `metadata.thread_id` 过滤同 thread 历史 | attrs `dr.thread_id`（baggage 传播） |
| trace URL 回显 | 后端 URL 模板（Jaeger / 自托管 Langfuse） |

**TBD-1 接入形态（后端与依赖）**：
- A 全自托管 OSS：Jaeger / Tempo + Grafana，零 Langfuse；
- **B（倾向）**：OTel 原生埋点 + 后端可插拔——默认 Jaeger 演示（零外部依赖），自托管 Langfuse 作可选
  OTLP 后端（保留 LLM 专属 UI 与 URL，W3 历史 trace 资产仍可用）；
- C 双轨保留（仅 Collector fan-out）：不满足「换成」目标，仅作兜底。
  取舍：B 代码层已是纯 OTel（满足叙事），且不丢 LLM 视图；Langfuse OTLP ingest 对 GenAI 语义约定的
  映射需 **spike 实测**后再写死。

**TBD-2 generation 埋点方式**：① 单点显式（`LLMClient.chat`，倾向——无 monkey-patch、符合自研薄封装
ADR、W3 已验证「唯一出口」结构事实）；② `opentelemetry-instrumentation-openai` 社区包（新增依赖、
隐式 wrap）；③ 自建全局 wrap（`langfuse.openai` 等价物，已有 W3.2 版本耦合教训）。

**TBD-3 trace / 线程模型**：OTel trace_id 同为 32-hex；生成策略（SDK 自动 vs seed 确定性）与同 thread
多次 run 唯一性；`thread_id` 属性与跨服务传播（Web worker `run_span("dr.run")` 与引擎根 span 的父子
关系）。

**TBD-4 成本口径**：generation span 记 token usage；成本行沿用 `format_cost_report`（定价表 + 保守
上界 + 人民币）；是否把估算成本写入 span attrs（`dr.cost_estimate_cny`）供后端展示（替代 Langfuse
cost 列）。

**TBD-5 URL 回显**：后端 URL 模板（Jaeger `{base}/trace/{trace_id}`；自托管 Langfuse `{host}/trace/{trace_id}`
形态待实测）；CLI `finally` 兜底逻辑（`graph.trace_id` 自持）保持不动。

**TBD-6 开关 / 隔离 / 降级**：延续三态（配置齐备自动启用 / `=false` 强制关 / 缺失自动降级）+
fail-silent + 常量进代码；单测零外发（conftest）；env 命名与 Web 侧统一。

**TBD-7 初始化与资源归属**：OTel 全局 TracerProvider 单次 `set`（`otel.py:61`）的进程内先后问题——
worker 先初始化则引擎 span 归属同一 provider；`service.name` 划分（research-engine /
deepresearch-worker）；CLI 自初始化路径。

**TBD-8 资产与依赖处置**：langfuse 依赖移除（或仅标注为「后端选项」）；openai 锁复评结论记录；
`verify_w3_trace.py` 重写（OTLP 后端 API 查询）；需求 3 注记「已被 28 反转」；README / 简历 / 截图；
是否需要一段双跑兼容期（倾向不需要，一次性切换 + 单测守卫）。

## 6. 设计策略

- 复用 W3 已验证决策：单点埋点、显式 span、flat 轮次 span、fail-silent、常量进代码——只换承载物；
- 需求 3 :53 的否决理由以今天资产看（Web 侧 `otel.py` 已跑通、OTel 六件套已在依赖）**不再构成阻塞**，
  问题主要是代价——由 ADR-0012 记录「接受代价」的拍板，不推翻 W3 的观测设计（span 结构 / 对账 /
  脱敏全部延续）；
- Langfuse 在 TBD-1 选项 B 中从「SDK 依赖」降为「可选后端」，语义从「接入方式」变成「部署选项」；
- 观测是旁路、不动主链路判定口径 ⇒ 无需 eval 基线；回归走 pytest + 冒烟。

## 7. 验收标准（DoD）

- [x] ADR-0012 建立（`docs/decisions/0012-otel-observability-migration.md`）；需求 3 变更记录追加「被需求 33 反转」注记
- [x] `research_engine/observability.py` 重写为 OTel 原生：根 span + 节点 span（attrs：node/round/
      depth/sq_id）+ generation span（gen_ai.* + usage）；单测断言引擎侧无 `langfuse` import（防回退）
- [x] 三态开关 + 知情打印 + fail-silent + 截断常量全部保留（能力不许退化）
- [x] OTLP 导出 + 后端可插拔说明（Jaeger 本地演示命令入 README / `.env.example`）
- [x] CLI/Web：URL 回显（后端模板）+ 成本行（人民币、定价表）+ 对账差口径不变
- [x] `tools/verify_otel_trace.py` 重写替代 `verify_w3_trace.py`（旧脚本已删除）
- [x] `egress.py` 数据去向披露项按新后端改写（`otel_configured` + `otel_endpoint_host`）
- [x] 真实冒烟：一次 research → span 树完整（含多轮 critic 循环），结论 + 截图入档（2026-10-11 完成，见 §9.1：86 spans / 62 gens / 对账 0 差 / verify PASS；截图 `local-artifacts/otel-smoke-2026-10-11-jaeger-trace.png`）
- [ ] 自托管 Langfuse OTLP ingest spike（TBD-1 选项 B 实测，随真实冒烟一并做）
- [x] README:21/89/390 更新；简历口径更新（落地才写，随收口）
- [x] 依赖清理（`requirements.txt` 移除 langfuse）与 openai 锁复评结论记录（暂留锁，TBD-8）；
      全量回归 1002 收集 = 715 通过 + 260 跳过 + 27 既有环境失败（与需求 27 基线逐项一致，零新增）

## 8. 影响范围与风险

- 动：`research_engine/observability.py`（重写）、`graph.py`（接线微调）、`llm/client.py`（generation
  单点）、`config.py`、`requirements.txt`、`cli.py`、`web/backend/egress.py`、`README.md`、
  `tests/test_observability.py`、`tools/verify_w3_trace.py`；`web/backend/otel.py` 主体不动。
- 风险①：OTel 全局 provider 单次 `set` 的进程内冲突（TBD-7 先验证 worker / CLI 两条路径）。
- 风险②：自托管 Langfuse 运维重（ClickHouse 等）→ 演示默认 Jaeger，自托管仅作选项。
- 风险③：迁移期双套并存口径漂移 → 一次性切换 + 单测守卫（grep 断言无 langfuse import）。
- 风险④：资产空窗（截图 / 博客②素材引用 Langfuse UI）→ 更新计划随批次 D 一并做。
- 风险⑤：openai 解锁引入回归 → 独立小 PR + 回归；可暂留锁、不阻塞迁移。
- 兜底：观测仍为旁路，未配置 / 后端不可达时零行为变化（fail-silent 延续）。

## 9. 测试策略

- 单测（离线）：开关三态、no-op、attrs 正确（node/round/thread）、URL 模板、对账差；
  `tests/test_observability.py` 改造 + `tests/test_otel.py` 保持。
- 冒烟（本地 docker）：`jaegertracing/all-in-one` → CLI 跑题 → 浏览器核对 span 树；
  可选 spike：自托管 Langfuse OTLP ingest。
- 回归：pytest 全量（注意 `--basetemp` 纪律，见 `docs/project-status.md:19`）；ruff。
- 守卫：单测断言 `research_engine` 无 `langfuse` import。

### 9.1 冒烟记录（2026-10-11，真实 CLI + 本地 Jaeger）

- **环境**：本地 `jaegertracing/all-in-one`（Docker：UI `:16686` / OTLP HTTP `:4318`）；
  `OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:4318`；
  `DR_OTEL_TRACE_URL_TEMPLATE=http://localhost:16686/trace/{trace_id}`；
  `PYTHONIOENCODING=utf-8`（Windows 重定向必带，见「发现 2」）。
- **预检（零 LLM）**：`start_trace + span_node` → Jaeger 2 spans 落库、URL 模板回显正确。
- **主冒烟**：`python cli.py "什么是RAG？"` → trace `3d9148055303491caaa629150d4fb082`
  （Jaeger：http://localhost:16686/trace/3d9148055303491caaa629150d4fb082）：
  - **86 spans / 62 generations**；span 序列含根 span（`deepresearch: 什么是RAG？`）、`规划`、
    `R1~R6 检索/裁决/修订`（多轮 critic 循环）、`写作/校验/修复引用/渲染`；
  - **usage 对账**：本地 `token_used = 198,449` ≡ 后端 `gen_ai.usage.total_tokens` 合计
    **198,449（差值 0）**；引用校验 85/85 存在性通过；
  - `tools/verify_otel_trace.py` 结论 **PASS**（6/6 结构断言）；
  - 截图存档：`local-artifacts/otel-smoke-2026-10-11-jaeger-trace.png`（gitignore 本地资产）。
- **冒烟发现（环境层，非迁移回归）**：
  1. **`.env` 的 MaaS 专属端点未部署 `qwen-turbo`** → fast 档 39 次调用全部
     `404 Model not exist`（OTel 错误 span 直接定位模型名）→ 预算按预占保守记账被烧穿、
     报告退化兜底（复现挂账 #154 现象）。进程内 `FAST_MODEL=qwen-plus` 复跑即恢复正常。
     **建议**：`.env` 显式 `FAST_MODEL=qwen-plus`，或在 MaaS 端点补部署 qwen-turbo。
  2. **Windows 重定向编码坑**：stdout 重定向时 Python 回退 GBK，emoji 打印（🔍/💰）
     抛 `UnicodeEncodeError`（实测复现）→ 跑 CLI 冒烟必须 `PYTHONIOENCODING=utf-8`；
     PowerShell `*>` 产出 UTF-16 日志 → `verify_otel_trace.py` 已加 BOM/编码自适应。

## 10. 排期（本批次）

> 口径：粗估按单主线开发节奏（人日），不含设计评审等待；周次绑定向 status board 收口后回填。

| 批次 | 内容 | 产出 | 依赖 | 粗估 |
|---|---|---|---|---|
| A | 设计评审（TBD-1~8 拍板）+ ADR-0012 + 需求 3 注记 | 拍板记录 / ADR | — | 0.5 天 |
| B | 引擎 OTel 埋点层重写 + 单测 | observability.py / tests | A | 1.5~2 天 |
| C | 导出与后端（Jaeger compose、URL 模板、知情打印、成本行、egress 披露） | 可跑链路 | B | 1 天 |
| D | 验证与资产（verify 脚本、真实冒烟、README/简历/截图、依赖清理） | 冒烟记录 / 文档 | C | 1 天 |
| E | 收口（回归、DoD 勾选、飞书同步） | 收口记录 | D | 0.5 天 |

合计 ≈ 4.5~5 人日；建议作为下一批次主线。

> **进度（2026-10-11）**：批次 A~E 主体完成——PR #181 已合入 dev（`7355ba4`）；真实冒烟完成（见 §9.1）；
> 剩余 = 自托管 Langfuse OTLP spike（批次 D 收尾）与飞书镜像同步（批次 E）。

## 11. 同期批次排期：对照 EvidenceFlow 的借鉴项（独立立项）

> 来源：EvidenceFlow 对照分析（§四 可借鉴 5 条 + 小注）；挂账编号见
> `docs/requirements/27-agent-audit-remediation.md` §12。**本表只做排期与依赖**；每项启动时按纪律
> 另立需求文档（编号 29+）并做设计评审。
> ⚠️ 纪律提示（W8 教训）：E1~E5 会改 researcher 证据面 → 是否需 **before 基线**（改动前采集，
> 错过不可逆）在该批次立项时先拍板。

| 编号 | 事项 | 映射 | 依赖 | 粗估 | 备注 |
|---|---|---|---|---|---|
| E0 | 工程杂项：默认分支切 dev（GitHub 设置）、31 个 open issue 分诊、本地 factsheet 清理（管理员 ACL） | 小注 | — | 0.5 天 | 随时可做，建议最先 |
| E1 | 网页正文快照落盘（search → read → extract） | 12.8 | — | 2~3 天 | `research_engine/net/safe_fetch.py` 已就绪未接入；快照存储 / 用户隔离 / 保留期须拍板 |
| E2 | 逐字引文 + offset 校验（Citation 扩展） | 12.6 前半 | E1 | 1.5~2 天 | >4000 头尾截断问题随之获解 |
| E3 | `find_in_source` 快照内二次定位 | 12.6 后半 | E1 / E2 | 1 天 | 长文档不重复联网 |
| E4 | 零模型交付自检 `delivery_self_check` | 新增 | E2（部分可先行） | 1 天 | 引文可定位 / 废弃段落 / 版本号来源；只标注不改交付状态 |
| E5 | 证据链分级处置（致命 vs 可修） | 新增 | E2 | 0.5~1 天 | 比「repair ≤1 统一降格」更细 |
| E6 | 跨进程 checkpoint + resume | 12.10 | E7 + 幂等 / 预算语义拍板 | 3~5 天 | 三语义（回放 / 重跑 / 续跑）先定 |
| E7 | 持久化 evidence manifest | 12.2 | 12.4（来源身份分组，另需 1~2 天） | 2~3 天 | — |
| — | 其余挂账（12.1 / 12.3 / 12.5 / 12.7 / 12.9 / 12.11） | 27 §12 | — | — | 维持挂账，不进本表 |

建议顺序：E0 → E1 → E2 →（E3 / E4 / E5）→ 12.4 → E7 → E6；其中 E1~E5 属同一个「证据链最后一公里」
交付单元，建议单独立项一篇需求同步评审。

## 12. 变更记录

| 日期 | 类型 | 原因 | 改动摘要 | 关联 PR/commit |
|---|---|---|---|---|
| 2026-10-11 | 文档 | 主理人拍板：可观测层对齐国内趋势，Langfuse → OTel | 建稿（草稿）：反转记录、现状盘点、TBD-1~8、DoD、排期（含同期 EvidenceFlow 借鉴项排期） | 本文档 |
| 2026-10-11 | 设计 | ADR-0012 | 反转记录建立：需求 3:53「OTel OTLP 出局」→ OTel 原生（代价接受清单 + W3 资产平移清单 + 影响范围） | `docs/decisions/0012-otel-observability-migration.md` |
| 2026-10-11 | 实现 | 需求 33 代码主体（Issue #180） | observability.py 重写（OTel 原生）；client.py generation 单点；graph.py 接线；`OtelConfig` 替换 LangfuseConfig；egress 披露；`verify_otel_trace.py` 替代 `verify_w3_trace.py`；requirements 移除 langfuse（openai 锁暂留）；tests 重写/适配 + 无 langfuse import 守卫；README / `.env.example` / 运维法务口径切换 | `feature/180-otel-observability-migration` / PR #181 |
| 2026-10-11 | 自测 | 本地全量回归（3.13.14 venv） | ruff 全过；pytest **1002 收集 = 715 通过 + 260 跳过（PG/Redis 缺失镜像 CI skip 语义）+ 27 既有环境失败（与需求 27 基线逐项一致，零新增）** | 本机 |
| 2026-10-11 | 同步 | 飞书镜像 | 已创建「需求设计文档：OTel 观测迁移」（docx `YHiEdRhNpoqRs4xUMyxcIFBHnXX`，经导入接口；`~` 转义版已核验）；正文在个人空间，待客户端移入 `DeepResearch 需求文档` 文件夹（同需求 27 情形） | 本文档 |
| 2026-10-11 | 合并 | 收口 | PR #181 CI 6/6 绿后 squash 合入 dev（`7355ba4`）；需求 33 状态置「已合」 | PR #181 / `7355ba4` |
| 2026-10-11 | 冒烟 | 真实 CLI + 本地 Jaeger | 见 §9.1：86 spans / 62 gens / usage 对账 0 差 / verify PASS；发现 .env MaaS 端点缺 qwen-turbo（环境层，非迁移回归）+ Windows 重定向编码坑（`PYTHONIOENCODING=utf-8` 必带）；`verify_otel_trace.py` 增加日志编码自适应 | 本文档 |
