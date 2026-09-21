# 需求 9：Web UI 重构（技术栈调研与选型）

> 状态：**§0~§7.7 已完成**（§6 = **D-20 拍板**，2026-09-20；§7 实现验证于 2026-09-21）；
> **§7.8 待补**（CI 前端 job 与边界守卫）。
> 调研日期：2026-09-19　设计日期：2026-09-20
> 前置：W8 已收尾冻结（`f723c2d`），本文档**不触碰** `research_engine/` 任何判定口径。

---

## 0. 背景与痛点

### 0.1 现状

`web/app.py` 为 **145 行单文件 Streamlit**（`streamlit 1.64.0`，依赖锁定见 §3.6）。
W8 Arm 1 已补齐失败展示（`run_status` 三态 + `degradation_log` 明细），需求 §2 遗留的
「侧边栏 slider 绑失效的 `max_depth/breadth`」也已修（现绑 `max_total_hops`）。

### 0.2 硬伤清单（读码所得，非印象）

| # | 问题 | 位置 | 严重度 |
| --- | --- | --- | --- |
| 1 | **进度条是假的**。`graph.run()` 同步阻塞（实测 **48~51 分钟/轮**），返回后才在循环里一次性刷完 ⇒ 用户按按钮后盯着 0% 与空白等 48 分钟，浏览器可能超时 | `app.py:74` → `95-97` | 🔴 致命 |
| 2 | **无 `session_state`** ⇒ 任何 rerun（点按钮、动 slider）报告/引用/溯源全部蒸发；`create_graph()` 每次重建 | `app.py:63` | 🔴 |
| 3 | 降级**事后**才可见。降级发生在运行中途，UI 只能在 `run()` 返回后展示 ⇒ 违背 W8「故障透明」初衷 | `app.py:82-88` | 🟠 |
| 4 | slider 每次 rerun 重置回 20 | `app.py:53` | 🟠 |
| 5 | **无法中断**，也无成本显示。跑起来只能关标签页，后台仍烧 token | `app.py:145` 仅显示 token | 🟠 |
| 6 | 摄取无状态：看不出知识库里已有哪些文档，靠 `doc_id=f.name` 幂等兜着 | `app.py:33-48` | 🟠 |
| 7 | 业务逻辑（`Counter`、计数、口径）混在 UI 里，**零测试覆盖** | `app.py:134-145` | 🟠 |

### 0.3 主理人诉求（原文转述）

1. 自己用着**感觉太单调**
2. **不利于维护**
3. 最主要——**显得有点粗糙**

### 0.4 已拍板

- **任务模型 = 前台跑 + 可取消**：页面开着可看实时进度、能随时中断。
  **不破 ADR-0001**（不做任务队列）；代价是关掉页面任务即丢失。

---

## 1. 市面主流 UI 技术栈全景（2026-09 核实）

### 1.1 A 类：Python 全栈框架（不写 JS）

| 框架 | 2026 状态 | 执行模型 | 长任务支持 | 视觉 | 生态位 |
| --- | --- | --- | --- | --- | --- |
| **Streamlit** | 1.55+ 为 2026-04 稳定版，Snowflake 主导、两周一版，社区最大 | 脚本**全量重跑**；`st.fragment`(2024+) 支持局部重跑 | ❌ rerun 模型天然不匹配；**~15 个 widget 后开始吃力**；多用户状态是公认弱项 | 朴素，深度定制需绕过 | 数据分析看板 / 快速原型 |
| **Gradio** | HF 主导，ML demo 事实标准 | 输入→输出映射，**事件驱动**（只跑被触发的函数） | 🟠 需自行卸载 | 主题可调 | 模型 demo、HF Spaces、ZeroGPU |
| **Dash**（Plotly） | 成熟、企业向 | 回调驱动（React 心智模型的 Python 版） | 🟠 轮询 | Full CSS 控制 | 企业分析应用、AG Grid 大表 |
| **NiceGUI** | ~15.5k stars，活跃（218 贡献者） | 事件回调 + **持久会话** | ✅ `background_tasks.create()` + `run.io_bound()`/`cpu_bound()` | Material/Quasar + Tailwind，100+ 组件；**自带测试框架** | IoT / 内部工具 / 控制面板（**非 ML demo**） |
| **Reflex** | ~28.3k stars，YC 支持，增长最快 | 响应式状态，编译到 **Next.js/React** | ✅ WebSocket | 高（CSS/React 可扩展） | 纯 Python 全栈应用；2~4 周上手，SSR 状态模型有 sharp edges |
| **Panel** | ~5.7k | 响应式变量 | ✅ | 中 | HoloViz 科学可视化 |
| **Flet** | 活跃 | Flutter 组件 | ✅ WebSocket | 中 | **跨平台**（Web+Desktop+Mobile） |
| **Mesop** | ~6.5k，Google 赞助减少 → 迁至 `mesop-dev` | — | — | — | 近期仅安全修复，**AI 相关包已移除** |
| Voilà / Solara / Shiny for Python | 小众 | Notebook 向 | — | — | — |

### 1.2 B 类：通用前端（需要 JS）

- **React / Next.js + Tailwind**：视觉与交互上限最高，生态最大。
- **HTMX / Alpine.js**：**无构建链**，服务端渲染 + 局部更新，配合 SSE 天然适合长任务推送。
- **传输层**：SSE（单向、够用、最简单）vs WebSocket（双向，支持 human-in-the-loop）。

### 1.3 C 类：Agent 专用（2026 的新变量，必须纳入考虑）

**AG-UI（Agent-User Interaction Protocol）** —— 由 CopilotKit 发起，把「Agent ↔ 前端」的通信
标准化成一套类型化事件流，等价于给这层定了个「HTTP」：

```
RUN_STARTED → STEP_STARTED → (消息 / 工具调用 / 状态同步 / 人工确认) → STEP_FINISHED → RUN_FINISHED
```

- **解决的问题**：此前每对「Agent 后端 × 前端」都要各写一套流式解析、状态同步、工具渲染；
  换 Agent 框架就得重写。
- **采纳者**（2026）：Google ADK、**LangChain**、AWS（Strands / Bedrock AgentCore）、
  Microsoft Agent Framework、Mastra、PydanticAI、CrewAI、LlamaIndex ⇒ 正在成为跨厂商事实标准。
- **LangGraph 有官方适配**（`agui-langgraph-agent`）。
- **前端 SDK 官方仅 React / Angular**（社区在做 Go / Rust / Java）。

> ⚠️ 与本项目直接相关：AG-UI 是**事件语义标准**，不是前端框架。
> **后端按它的事件名吐流，并等于要上 React**。这一点在 §4 里是免费的期权。

---

## 2. 同类项目用什么技术栈（主理人特别要求核实）

| 项目 | 状态（2026-05~09 核实） | Agent 层 | 后端 | 前端 | 实时通道 |
| --- | --- | --- | --- | --- | --- |
| **GPT Researcher** | 28~30k stars，semi-alive（211 贡献者；173 open issues 几无响应） | LangGraph + MCP | **FastAPI** | **双前端策略**：① Vanilla JS 静态页（由 FastAPI 托管，**无构建**）② **Next.js 14 + React 18 + TS + Tailwind**（生产） | **WebSocket**（`websocket_manager.py`：`asyncio.Queue` + sender task；30s 心跳；移动端降级 REST `/api/chat`） |
| **Local Deep Research**（LearningCircuit） | 活跃（46 贡献者，高频提交） | LangChain + LangGraph | **FastAPI** + FAISS + SQLCipher | Python 侧 Web UI | — |
| **DeerFlow**（字节） | 活跃 | LangChain + LangGraph | — | Web UI | — |
| **STORM**（Stanford） | ❌ **abandoned**（最后提交 8 个月前） | DSPy + LiteLLM | — | **Streamlit** | — |
| **Open Deep Research**（LangChain） | ❌ **abandoned**（最后人类开发 2025-08） | LangGraph supervisor | — | **无正式前端**（Python + Jupyter notebook） | — |
| **Open Deep Research**（Together） | ❌ abandoned（仅 3 commits） | — | — | 无 | — |
| **HuggingFace smolagents ODR** | 28k（库本体） | smolagents `CodeAgent` | — | 无前端 | — |

### 2.1 三条硬结论

1. **主流范式 = FastAPI 后端 + WebSocket 实时流 + 前端分层**。
   GPT Researcher 是唯一把前端做完整的同类项目，且用「**双前端**」同时覆盖
   轻量（无构建静态页）与生产（Next.js）两种场景。
2. **用 Streamlit 的同类项目（STORM）维护状况最差**；仍在迭代的项目都自己写 Python 后端 + Web 前端。
   ⚠️ 这条是相关性不是因果（STORM 停更多半因学术项目周期），但**它确实说明 Streamlit 不是这个品类的选择方向**。
3. **没有任何主流 DeepResearch 项目用 NiceGUI / Chainlit / Gradio 做主力前端** ——
   这三者的生态位分别是 IoT 内部工具、聊天机器人、模型 demo，与「长流程研究工具」不对位。

---

## 3. 本项目的硬约束（筛选器）

| # | 约束 | 来源 |
| --- | --- | --- |
| 3.1 | 单轮 **48~51 分钟**（after 基线实测）⇒ 必须长连接 + 可取消 | `docs/eval-w8-after-baseline.md` |
| 3.2 | 任务模型已拍板**前台跑 + 可取消** ⇒ 不做任务队列，**不破 ADR-0001** | 主理人拍板 |
| 3.3 | **ADR-0001**：不做完整产品（Web UI / 用户系统 / 任务队列等外围功能），聚焦核心链路 ⇒ **不引入 npm 构建链** | `docs/decisions/0001-initial-design.md:19` |
| 3.4 | **W8 冻结纪律**：不碰 `research_engine/` 判定口径；新增流式接口**必须复用** `run()` 的 `_recover_from_exception`（`graph.py:348`），否则会造出第二条「故障不可归因」路径，把 A 类验收的 100% 可归因率打折 | `docs/eval-w8-dod.md` |
| 3.5 | **CLI 仍是第一公民**（跑批、eval 全靠它），UI 重构不得影响 CLI | 项目惯例 |
| 3.6 | venv Python 3.13.14，依赖已锁定：`langgraph 1.2.11` / `pydantic 2.13.5` / `langfuse 4.15.3` / `openai 1.92.3` / `streamlit 1.64.0` / `qdrant-client 1.19.1` ⇒ 新增依赖**必须先做冲突预检** | §8 同构性核验 |
| 3.7 | 可行性已确认：`graph` 是 `compile(checkpointer=MemorySaver())`（`graph.py:89`），LangGraph 原生支持 `.stream()` ⇒ **真流式无需改图结构** | 读码确认 |

---

## 4. 候选对比与推荐

### 4.1 对比表（列顺序按主理人三个痛点排序）

| 维度 | **FastAPI + SSE + HTMX/Tailwind** | NiceGUI | FastAPI + React/Next.js | Streamlit 原地改造 |
| --- | --- | --- | --- | --- |
| 视觉「不粗糙」 | 🟠 Tailwind 可做出现代感，但组件要手写 | ✅ Material/Quasar 100+ 组件开箱 | ✅ 上限最高 | ❌ 基本解决不了 |
| 可维护「不单调」 | ✅ 路由/模板/服务层分离清晰 | ✅ 组件化 + 持久会话 | ✅ 但双语言维护成本 | 🟠 仍受 rerun 模型约束 |
| 48 分钟长任务（硬约束 3.1） | ✅ SSE 原生 + 可取消 | ✅ `background_tasks` | ✅ | ❌ |
| 纯 Python、无 npm（约束 3.3） | ✅ | ✅ | ❌ | ✅ |
| UI 逻辑可测 | 🟠 需自建（但已分离即可测） | ✅ 自带测试框架 | 🟠 需自建 | ❌ |
| **同类项目印证（§2）** | ✅✅ **唯一有印证**（GPT Researcher 的 FastAPI+静态前端方案） | ❌ 无 | ✅ GPT Researcher 生产前端 | ❌ STORM（已停更） |
| **Align AG-UI 的可能性** | ✅ 事件流自己写，可完全对齐标准 | ❌ 事件流锁死在框架内 | ✅（CopilotKit 原生） | ❌ |
| 活跃度 / 安全 | ✅ | ✅ 活跃 | ✅ | ✅ |
| 迁移成本（145 行起） | 中（后端 ~120 行 + 模板 ~200 行） | 中（~250 行） | 高（npm 链 + 双栈） | 低（~120 行） |

### 4.2 初轮推荐：FastAPI + SSE + 轻量前端（HTMX + Tailwind），事件语义对齐 AG-UI

> ⚠️ 本节是我基于第一轮调研的推荐。**主理人倾向 FastAPI + React**，专项可行性分析见 **§5**。
> 两轮的差异不在对错，而在权重：本节把「少写代码 / 不引构建链」排在最前，
> §5 把「视觉质感 / AG-UI 生态」排在最前。**最终以 §6.1 拍板为准。**

五条理由：

1. **唯一被同类项目印证的路线**（§2.1-1）—— GPT Researcher 的「FastAPI + 无构建静态前端」
   方案与本项目约束（3.3 不引入 npm）几乎完全重合。
2. **纯 Python、零构建链**，不破 ADR-0001。
3. **SSE 天然解决 48 分钟长任务 + 真流式 + 可取消**（硬约束 3.1 + 已拍板任务模型）。
4. **视觉不粗糙**：Tailwind 可做出与 Next.js 前端同一档次的观感，且无 npm 代价。
5. **免费期权 —— 后端事件流按 AG-UI 命名**（`RUN_STARTED` / `STEP_STARTED` / `STEP_FINISHED` /
   `RUN_FINISHED`）：AG-UI 是事件语义标准而非前端框架（§1.3），**后端按标准吐流 ≠ 要上 React**。
   将来若要换 React + CopilotKit 前端，**后端一行不用改**。附带好处：这是能在面试里讲清楚
   的「按行业标准对接」决策，而不是「我自创了一套协议」。

### 4.3 诚实修正：撤回上一轮的 NiceGUI 推荐

上一轮我推荐 NiceGUI，理由是它的 `background_tasks` 与持久会话正好命中硬伤 1/2。
本轮调研后**撤回**，两条理由：

1. **§2.1-3：无任何主流 DeepResearch 项目用它做主力前端**，生态位是 IoT / 内部工具。
2. **事件流会被锁死在框架内**，无法对齐 AG-UI ⇒ 失去 §4.2-5 的免费期权。

NiceGUI 仍然是「最省事的 Python 纯度方案」，若主理人把「少写代码」的权重排在
「对齐行业标准」之上，可以翻回它 —— 这是权重取舍，不是对错。

### 4.4 已排除

- **Chainlit**：2.12.0（2026-08-25）是安全修复版，修 **CVE-2026-45018（CVSS 9.8，MCP stdio
  命令注入）+ CVE-2026-45019（SSRF）**；原团队 2025-05 退出、现社区维护，版本节奏 4~8 周 → 4 个月；
  且**它是聊天范式**，本项目是「主题 → 报告」。
- **FastAPI + React/Next.js**：视觉上限最高，但与 ADR-0001 正面冲突（约束 3.3），
  且引入 npm 双栈维护。除非本项目要往「产品」方向推。
- **Gradio**：输入→输出范式，无真网格布局，不适合长流程。
- **Reflex / Dash / Panel / Flet**：生态位（全栈应用 / 企业分析 / 科学可视化 / 跨平台）
  均与本项目不对位，且学 2~4 周不划算。

---

## 5. FastAPI + React 专项可行性分析（主理人倾向方案）

> 主理人倾向 FastAPI + React。本节把可行性、代价与风险全部落到**实测**与**具体行数**，
> 并如实标注一个不能绕开的技术真相（§5.4 取消粒度）。

### 5.1 依赖冲突预检（2026-09-19 实测，非估计）

命令：`pip install --dry-run --report` 于 venv `python 3.13.14`，目标 `fastapi>=0.115` + `uvicorn>=0.30`。

| 结论 | 实测 |
| --- | --- |
| **新增包** | **仅 2 个**：`fastapi==0.141.1`、`annotated-doc==0.0.5` |
| `pydantic` | 2.13.5 **✅ 不动** |
| `langgraph` / `langfuse` / `openai` / `qdrant-client` / `streamlit` | 1.2.11 / 4.15.1 / 1.92.3 / 1.19.0 / 1.63.0 **✅ 全部不动** |
| 已存在、无需安装 | `uvicorn==0.52.4`、`starlette==1.6.0`、**`websockets==16.1.1`**（⇒ **WebSocket 也是现成的**） |
| ⚠️ 唯一踩到的坑 | `uvicorn[standard]` **装不上**（ResolutionImpossible）—— 该 extra 含 `uvloop`，**Windows 不支持**。用不带 extra 的 `uvicorn` 即可（Windows 下走 asyncio 而非 uvloop，功能不受影响） |

**⇒ 依赖风险 ≈ 0**。这是本轮调研最有利的一条：原本担心的「新增依赖污染锁定环境」不成立。

### 5.2 运行环境可用性（实测）

| 项 | 状态 |
| --- | --- |
| Node | **v22.22.2**（managed）+ npm / npx 就绪 |
| Python | venv 3.13.14，uvicorn + starlette + websockets 已装 |
| CI | 现为**单 job**（`lint-and-test`，Python 3.11/3.12/3.13 matrix，约 110s）⇒ 需**新增一个前端 job** |

### 5.3 架构草案

```
web/
├── backend/                  # FastAPI（纯 Python，进 ruff/pytest 管辖）
│   ├── main.py               # app 装配 + 静态资源托管（构建后的 frontend/dist）
│   ├── routers/
│   │   ├── research.py       # POST /api/research（启动，返回 run_id）
│   │   │                     # GET  /api/research/{run_id}/stream（SSE）
│   │   │                     # POST /api/research/{run_id}/cancel（协作式取消）
│   │   ├── rag.py            # POST /api/rag/ingest（上传）+ GET /api/rag/docs（已摄取清单）
│   │   └── health.py
│   ├── agui.py               # AG-UI 事件封装：RUN_STARTED / STEP_STARTED / STEP_FINISHED / RUN_FINISHED
│   └── runner.py             # 包装 iter_run()，管理取消标志与线程池
└── frontend/                 # React + Vite（npm 管辖）
    ├── src/
    │   ├── hooks/useResearchStream.ts   # SSE 消费（EventSource 或 fetch 流式读）
    │   ├── components/                  # ResearchForm / ProgressTimeline / DegradationPanel
    │   │                                # / ReportView / CitationList / TracePanel / CostMeter
    │   └── types/agui.ts                # AG-UI 事件类型
    ├── package.json / tsconfig.json / vite.config.ts / tailwind.config.js
    └── dist/                            # 构建产物，由 FastAPI 托管（gitignore）
```

**关键选型：Vite SPA，不是 Next.js。**
GPT Researcher 生产前端用 Next.js，但它的动机是产品化部署（SEO、路由、SSR）。
本项目是**单页内部工具**：无 SSR 需求、无 SEO、无多路由 ⇒ Next.js 只带来服务端运行时复杂度，
换来零收益。**Vite + React 18 + TS + Tailwind** 是同一套开发体验、更轻的产物。

**AG-UI 怎么用：对齐语义，但先不引 SDK。**
CopilotKit（`@copilotkit/react-core`）是**聊天范式封装**，而本项目是「表单提交 → 报告产出」。
⇒ 后端按 AG-UI 事件名吐 SSE，前端用 `EventSource` 自行消费（约 60 行）。
将来若要做 Generative UI / 共享状态 / human-in-the-loop，再引 CopilotKit，**后端一行不改**。

### 5.4 ⚠️ 必须说清的技术真相：**取消是协作式的，不是真中断**

D-19 拍板的硬约束是「取消必须真能停掉后端，否则只是关页面后台继续烧 token」。
但这里有一条**绕不过去的语言级限制**，必须如实记录：

- `graph.stream()` 是**同步生成器**，必须在线程池里跑（`run_in_executor`）。
- **Python 无法强杀线程**（无安全的 Thread.kill）⇒ 正在执行的那个节点**中断不了**。
- ⇒ 可实现的只是**协作式取消**：在节点边界检查取消标志，置位则 `break` 出 stream 循环，
  后续节点不再执行；`asyncio` 侧可 `cancel()` 掉等待。

| 项 | 实际表现 |
| --- | --- |
| 取消**粒度** | **节点边界**（不是毫秒级） |
| 最坏延迟 | 多等**一个节点**（一次 LLM 调用，实测单节点量级为数十秒） |
| 已产出内容 | 保留在 `MemorySaver` checkpoint 中，取消后仍可展示半程结果 |
| 是否省下钱 | ✅ 能 —— 48 分钟的流程在第 3 分钟取消，就只付 3 分钟的钱 |

**这个限制不是 React 方案的代价**：HTMX / NiceGUI / Streamlit 方案下同样存在，
因为瓶颈在「LangGraph 同步执行 + Python 无法杀线程」，与前端选型无关。
⇒ 若主理人期望「点取消立刻停」，那**没有任何前端方案能做到**，需要的是把节点内部改成可中断
（那是核心链路改造，超出 UI 重构范围，且会触碰 W8 冻结的判定口径）。

### 5.4.1 节点耗时实测（2026-09-20）：**暂不需要拆节点**

取消契约有一条是「如果某个节点经常运行数分钟，就拆成更小的可检查步骤」——
这需要实测才能判定，不能拍脑袋。

**数据**：after 基线 3 轮 + 冒烟 run，共 **62 题**的 `wall_clock_s` 与 `progress` 条目。

**口径校准（先证 `progress` 条目 = graph 节点）**：抽样一题的 stage 序列为

```
plan → (research → critic → revise) × 6 → research → critic → write → validate → render
```

24 条 `progress` = 24 个连续段、**每段 ×1** ⇒ **`progress` 条目与 graph 节点执行 1:1**，
故 `wall_clock_s / 节点数` 是可靠口径。

| 指标 | 单节点耗时估算（62 题） |
| --- | --- |
| 中位数 | **14.4 s** |
| 均值 | 15.0 s |
| p90 | 17.7 s |
| 最大 | 31.4 s |

**⇒ 结论：没有任何节点跑到「数分钟」量级，最坏等待约 30 秒**
⇒ **取消契约中「拆成更小步骤」这一条暂不触发。**

⚠️ **口径局限（必须标注）**：上述是 `wall_clock / 节点数` 的**均值估算**，隐含
「各节点耗时均匀」的假设。真实分布并不均匀——尤其 `write` / `validate` / `render`
等尾部节点可能显著长于均值 ⇒ 表中数值应视为**乐观下界**。
**行动项**：W9 实施时给 `progress` **补节点级时间戳**（新增字段，不改任何判定口径），
用真实数据校准本表。

### 5.5 优缺点

**优点**

| # | 优点 | 说明 |
| --- | --- | --- |
| 1 | **视觉上限最高** | 痛点 3「显得粗糙」彻底解决；Tailwind + 自定组件可做到与商业产品同档观感 |
| 2 | **唯一能完整吃 AG-UI 生态的方案** | AG-UI 的前端 SDK **官方只有 React / Angular**（Angular 生态不对位）⇒ CopilotKit 的 Generative UI、双向共享状态、human-in-the-loop **全都要 React**。这是 HTMX / NiceGUI 拿不到的红利 |
| 3 | **与行业主流对齐** | GPT Researcher 生产前端 = Next.js + React + Tailwind；Local Deep Research 亦为 Web 前端 |
| 4 | **前后端分离清晰** | 痛点 2「不利于维护」：UI 逻辑进 `frontend/`、可单测（Vitest）、与 `research_engine/` 边界干净 |
| 5 | **面试价值** | 全栈能力可被直接展示（主理人已有 React/TS 基础），且「按 AG-UI 标准对接」是可讲的工程决策 |

**缺点与代价**

| # | 代价 | 量级 | 可否缓解 |
| --- | --- | --- | --- |
| 1 | **违反 ADR-0001**（不做完整产品、不引入外围复杂度） | 项目级决策 | ✅ 可化解，见 §5.6 |
| 2 | 双栈维护：两套依赖 / lint / test / CI | 持续成本 | 🟠 只能靠纪律约束 |
| 3 | npm 构建链与供应链（`package-lock.json` 必须入库、CI 用 `npm ci`） | 一次性 + 持续 | ✅ 可控 |
| 4 | `node_modules` 体积（数百 MB） | 磁盘 | ✅ gitignore 即可 |
| 5 | 工作量约 **2.5 倍**于 HTMX 方案 | 见 §5.7 | — |
| 6 | 仓库性质从「纯 Python 研究项目」变「全栈项目」 | 定位 | ⚠️ 见 §5.6 的边界声明 |

### 5.6 ADR-0001 冲突能否化解（我的判断：**能**）

ADR-0001 原文：*不做完整产品（Web UI、用户系统、任务队列等外围功能），聚焦核心链路。*

关键在于区分两件事：**「做什么」** 与 **「用什么做」**。
ADR 反对的是**产品化外围功能**（用户系统、任务队列、多租户、部署体系），
而不是**呈现层的技术选型**。用 React 写呈现层，只要不做用户系统 / 任务队列 / 多租户，
就**没有越界**——它仍然是「聚焦核心链路」的研究工具，只是界面好看些。

⇒ **建议做法**：不推翻 ADR-0001，而是**追加一条修订**，显式划出边界：

> **ADR-0001 修订**：允许引入前端构建链（Vite + React）作为**呈现层**选型。
> 但**不做**用户系统、任务队列、多租户、权限、云端部署。
> 前端仅消费后端 AG-UI 事件流，不得承载任何核心链路逻辑（所有 Agent 编排仍在 `research_engine/`）。

这样既拿到 React 的红利，又把「不做完整产品」的约束**固化成可检查的边界**
（甚至可以写成 CI 检查：前端目录不得 import `research_engine`）。

### 5.7 工作量估算

| 部分 | 行数 | 说明 |
| --- | --- | --- |
| 后端（`web/backend/`） | ~250 | 路由 + AG-UI 事件封装 + 取消管理 + 线程池 |
| 新增 `iter_run()`（`research_engine/graph.py`） | ~60 | **新增不改旧**，复用 `_recover_from_exception`（硬约束 3.4） |
| 前端组件（`web/frontend/src/`） | ~700~900 | 7 个组件 + SSE hook + 类型定义 |
| 配置（package.json / tsconfig / vite / tailwind / eslint / vitest） | ~150 | — |
| CI 新增前端 job | ~30 | setup-node 22 + `npm ci` + `tsc` + build |
| 测试（后端 pytest ~150 / 前端 Vitest ~100） | ~250 | 前端测试可选 |
| **合计** | **~1450~1650** | 对比 HTMX 方案（~500~600）⇒ **约 2.5~2.8 倍** |

### 5.8 风险清单（按严重度）

| 级别 | 风险 | 缓解 |
| --- | --- | --- |
| 🟠 高 | **SSE 长连接被中间层掐断**：反向代理（nginx 默认 `proxy_read_timeout` 60s）会断掉 48 分钟的流 | 本地直连无此问题；**必须加心跳**（每 15~30s 发 `: ping`）—— GPT Researcher 用 30s 心跳，可直接借鉴。将来部署时再调代理超时 |
| 🟠 高 | **SSE 断线重连语义**：`EventSource` 原生会自动重连，若服务端无状态会**重跑整个研究**（= 双倍烧钱） | 用 `run_id` 幂等 + 服务端缓存事件序列；或改用 fetch 手动重连并携带 `Last-Event-ID` |
| 🟡 中 | 双栈 lint / test 纪律容易退化 | CI 双 job 强制；`package-lock.json` 入库 |
| 🟡 中 | 依赖预检是 `--dry-run`，实际安装可能仍有差异 | 落地第一步就真实安装并跑通冒烟，再动业务代码 |
| 🟢 低 | Windows 下 `uvicorn[standard]` 不可用 | 已定位：用不带 extra 的 `uvicorn`（§5.1） |

### 5.9 我的建议：**可以做，但附三个前置条件**

主理人的倾向在本项目语境下是**站得住的**——尤其 §5.5-2（AG-UI 生态只有 React 能吃满）
这条是我上一轮低估的：AG-UI 前端 SDK 官方仅 React/Angular，意味着选 HTMX 就等于
**主动放弃**整个 AG-UI 生态。

但若决定走 React，我建议先接受三条约束，否则容易做成「产品」而偏离研究工具定位：

1. **Vite SPA，不上 Next.js**（省掉 SSR 运行时，本项目零收益）。
2. **对齐 AG-UI 语义，先不引 CopilotKit**（它是聊天范式封装，我们是表单→报告范式；
   保留将来引入的口子即可，后端不改）。
3. **先修订 ADR-0001 划边界**（§5.6），把「不做用户系统 / 任务队列 / 多租户」写成可检查的硬约束。

---

## 6. 拍板结论（**D-20**，2026-09-20 主理人拍板）

> **W9 采用 FastAPI + React/Vite 作为呈现层技术栈，使用 SSE 传输并对齐 AG-UI 事件语义；
> 不引入 CopilotKit、任务队列、用户系统或多租户。取消采用节点边界上的协作式取消，
> 保证取消请求后不启动新的研究节点和 LLM 调用。W9 不修改 `research_engine/` 的核心判定口径。**
>
> **—— React 是呈现层升级；核心研究链路、任务模型和项目边界保持不变。**

选型理由不是「React 更潮」，而是它最直接命中三个真实痛点：**视觉粗糙 / 难维护 / 长任务无实时反馈**，
且依赖预检（§5.1）已证明不会破坏核心环境。

### 6.1 拍板表

| 决策点 | 拍板 | 状态 |
| --- | --- | --- |
| 后端 | **FastAPI** | ✅ |
| 前端 | **React + Vite + TypeScript** | ✅ |
| 样式 | **Tailwind** | ✅ |
| 传输 | **SSE** | ✅ |
| 事件协议 | **对齐 AG-UI 语义，但先不引 CopilotKit** | ✅ |
| 前端形态 | **Vite SPA，不用 Next.js** | ✅ |
| Streamlit | **迁移完成后直接替换，不长期双维护** | ✅ |
| 取消 | **接受协作式取消，不追求毫秒级强杀** | ✅（契约见 §6.2） |
| ADR | **追加修订，不推翻** | ✅ 已落地于 `docs/decisions/0001-initial-design.md` §1.1 |
| 任务模型 | **仍为前台运行**；不引入任务队列、用户系统、多租户 | ✅ |
| 节点耗时 / 是否拆节点 | **暂不拆**（实测最大 ~31 s，见 §5.4.1） | ✅ |

### 6.2 取消契约（**必须按此实现，不得写成模糊的「支持取消」**）

核心表述：**取消请求立即生效；执行停止在下一个安全边界。**

| # | 契约条款 |
| --- | --- |
| C1 | 用户点击取消后，前端**立即**显示「正在停止」 |
| C2 | 后端**不再启动下一个节点** |
| C3 | 当前正在执行的同步节点**允许自然结束** |
| C4 | 当前节点结束后，研究流程**停止** |
| C5 | 取消后**不再产生新的 LLM 调用** |
| C6 | 最坏等待时间 = **当前节点剩余执行时间**（实测估算量级：中位 ~14 s，最大 ~31 s，见 §5.4.1） |
| C7 | 若某节点经常运行数分钟 ⇒ 拆成更小的可检查步骤（**实测暂不触发**，见 §5.4.1） |

> ⚠️ 接受的不是「取消按钮看起来能用」，而是上述可逐条验收的契约。
> **React / HTMX / NiceGUI 都不能凭前端技术把同步 Python 线程强杀掉**，
> 所以这个限制**不应归咎于 React**（§5.4）。

### 6.3 三条硬边界（给 React 上的锁）

1. **前端不承载研究逻辑** —— 前端只能展示状态、提交配置、发送取消请求，
   **不得复制 `research_engine` 的判定逻辑**。
   可写成 CI 检查：前端目录不得 import `research_engine`。
2. **不引入产品化功能** —— 不做登录、用户系统、任务队列、持久化任务、多租户、权限、云端部署。
3. **W9 完成标准封顶**（§6.4）—— 不做清单内的东西就一律不做。

### 6.4 W9 完成标准（**只实现这 7 项**）

- [x] 提交研究
- [x] 实时阶段进度
- [x] 降级事件展示
- [x] token / cost 展示（后端未提供 cost 时明确显示“不估算”）
- [x] 取消（按 §6.2 契约）
- [x] 报告与引用展示
- [x] 文档摄取状态（展示本轮 RAG 命中；当前协议不伪造上传/摄取进度）

**明确不做**（本次一律不启动）：聊天框、复杂工作台、历史任务中心、CopilotKit。

### 6.5 何时应当改选 HTMX（反悔条件，存档备用）

只有在以下**任一**条件成立时，才应改选 HTMX：

- 只想用最少时间修掉假进度条；
- 不想维护 Node / npm / 前端测试链；
- UI 只是临时工具，不准备作为项目展示的一部分；
- 愿意牺牲视觉上限与未来 AG-UI / 人工介入扩展能力。

否则 HTMX 更省工，但它解决的是「快速把功能补上」，
**不完全解决**最在意的「粗糙」和「维护体验」两条。

### 6.6 ADR 处置

已**追加** `docs/decisions/0001-initial-design.md` **§1.1 修订**（不推翻原 ADR），
把「轻量 Web UI」解释为：

> **允许升级呈现层技术栈，但不扩展产品边界。**

即「**用什么做**可以升级，**做什么**不得扩展」。与 ADR 原有理由
（「面试官最看重核心编排深度和可量化结果，而非 UI 完整度」）**不冲突** ——
W9 不动核心链路与任务模型，UI 仍不是本项目卖点，只是不再成为短板。
变更记录已同步登记 2026-09-20 一行。

---

## 7. 详细设计

> 实施顺序（主理人指定）：**① `iter_run()` 契约 → ② SSE/AG-UI 事件协议 → ③ 取消机制 →
> ④ FastAPI + React 最小骨架 → ⑤ 视觉 / 测试 / CI**。
> **尤其不要先做视觉组件** —— `iter_run()` 与事件协议才是整个 W9 的地基。

### 7.1 `iter_run()` 契约（地基第一块）

#### 7.1.1 硬约束 recap

- **新增不改旧**：`run()`（CLI / eval 依赖）**保持零改动**，新增 `iter_run()` 方法。
- **异常路径必须复用 `_recover_from_exception`**（`graph.py:348`），否则会造出第二条
  「故障不可归因」路径，把 W8 A 类验收的 100% 可归因率打折（约束 3.4）。
- **不新增 `ResearchState` 字段语义**：只读取，不改判定口径。

#### 7.1.2 签名

```python
# research_engine/graph.py —— 新增方法（不改动 run()）

def iter_run(
    self,
    topic: str,
    user_instructions: str = "",
    thread_id: str | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> Iterator[RunStep]:
    """流式运行：每完成一个 graph 节点 yield 一次快照。

    与 run() 的关系：run() = iter_run() 的「只取最后一个快照」特化版（但不改写 run()）。
    异常路径与 run() **共用** _recover_from_exception，保证故障可归因率不被稀释。
    取消路径 **不共用**（见 §7.3.3 —— 取消不是故障）。
    """
```

`RunStep` 定义在**新文件** `research_engine/streaming.py`（不污染 `state.py` / `graph.py` 的判定口径）：

```python
@dataclass(frozen=True)
class RunStep:
    index: int                                   # 节点序号（0-based）
    node: str | None                             # "plan"/"research"/"critic"/"revise"/"write"/"validate"/"render"
    state: ResearchState                         # 该节点完成后的**完整**快照
    new_degradations: tuple[DegradationEntry, ...]   # 本节点新增的降级条目（增量）
    duration_ms: int                             # 节点耗时 —— 落地 §5.4.1 的时间戳行动项
    terminal: bool = False                       # 是否为终局 step
    stop_reason: str = "running"                 # "running" | "completed" | "cancelled" | "error"
```

#### 7.1.3 取节点名：用 `progress[-1]["stage"]`，不用多模式流

LangGraph 1.2.11 的 `StreamMode` 取值为
`values | updates | checkpoints | tasks | debug | messages | custom`（已核 `langgraph/types.py:122`）。
两种单模式各有缺口：

- `updates` 给节点名但**只给 delta**，拿不到完整 state；
- `values` 给完整 state 但**不给节点名**。

⇒ **采用 `stream_mode="values"` + 从 `state.progress[-1]["stage"]` 取节点名。**
依据是 §5.4.1 的实测：**`progress` 条目与 graph 节点执行严格 1:1**
（24 条 = 24 段，每段 ×1），故 `progress[-1]["stage"]` 就是刚完成的节点。

> ⚠️ **实施第一步必须先实测确认**：`values` 模式的首帧语义（是否含初始 state、此时
> `progress` 为空 ⇒ `node=None`）。这是本设计里唯一未实证的假设，确认成本 5 分钟。

#### 7.1.4 终止原因与异常路径

沿用 `run()` 已有的实例属性惯例（`self.last_exception`、`self.tokens_diff`）⇒
新增 **`self.last_stop_reason: Literal["completed", "cancelled", "error"]`**。

```python
try:
    for i, chunk in enumerate(self.graph.stream(initial, cfg, stream_mode="values")):
        state = ResearchState(**chunk) if isinstance(chunk, dict) else chunk
        node = state.progress[-1]["stage"] if state.progress else None
        yield RunStep(index=i, node=node, state=state,
                      new_degradations=self._diff_degradations(state),
                      duration_ms=..., )
        if should_cancel is not None and should_cancel():   # ← 在 yield **之后**检查（C3）
            self.last_stop_reason = "cancelled"
            break                                            # ← 不再启动下一节点（C2/C5）
    else:
        self.last_stop_reason = "completed"
except Exception as exc:                                     # noqa: BLE001
    # 异常路径：与 run() 共用同一套兜底，绝不另造
    self.last_exception = exc
    state = self._recover_from_exception(exc, cfg, initial)
    self.last_stop_reason = "error"
    yield RunStep(index=-1, node=None, state=state, ..., terminal=True, stop_reason="error")
```

**降级增量**：`degradation_log` 用的是 `operator.add` reducer（`state.py:185`），
⇒ `_diff_degradations()` 只需按已推条目数做切片，无需比较内容。

### 7.2 SSE / AG-UI 事件协议（地基第二块）

#### 7.2.1 事件映射表

AG-UI 标准事件：`RUN_STARTED` / `STEP_STARTED` / `STEP_FINISHED` / `STATE_SNAPSHOT` /
`STATE_DELTA` / `TEXT_MESSAGE_*` / `TOOL_CALL_*` / `RUN_FINISHED` / `RUN_ERROR`。

| AG-UI 事件 | 触发时机 | payload |
| --- | --- | --- |
| `RUN_STARTED` | 研究启动 | `run_id`, `topic`, `config{max_total_hops, planner_model, ...}` |
| `STEP_STARTED` | 收到下一节点名时（可选，先不做） | `node`, `index` |
| `STEP_FINISHED` | 每个 `RunStep` yield | `node`, `index`, `duration_ms`, `depth`, `token_used` |
| `STATE_DELTA` | 随 `STEP_FINISHED` 同发 | `progress` 新增项、`findings` 增量、`visited_sources` 增量 |
| `DEGRADATION` ⚠️**自定义扩展** | `new_degradations` 非空时 | `node`, `component`, `reason`, `detail`, `fallback_action` |
| `RUN_FINISHED` | 正常结束**或取消结束** | `run_status`, **`cancelled: bool`**, `token_used`, `stop_reason` |
| `RUN_ERROR` | `stop_reason == "error"` | `code`, `message`, `node`（取自 `_recover_from_exception`） |

> ⚠️ **AG-UI 没有「降级」事件** ⇒ `DEGRADATION` 是本项目的**自定义扩展**。
> AG-UI 协议本身支持 custom events（它标准化的是事件生命周期而非事件全集），
> 因此这**不破坏对齐**。且这条正是硬伤 3（降级事后才可见）的解药。

#### 7.2.2 传输细节

| 项 | 设计 | 依据 |
| --- | --- | --- |
| Content-Type | `text/event-stream` | — |
| **心跳** | 每 **15 s** 发 SSE 注释 `: ping\n\n`（**不是事件**，不触发前端 `onmessage`） | nginx 默认 `proxy_read_timeout` 60 s，15 s 留 4 倍余量；GPT Researcher 用 30 s 心跳作参照 |
| 断线重连 | 前端携带 `Last-Event-ID`；服务端按 `run_id` 缓存事件序列（内存 dict，设上限） | 防 `EventSource` 自动重连**重跑整个研究**（= 双倍烧钱），风险 §5.8 |
| 路由 | `POST /api/research` 启动返回 `run_id`；`GET /api/research/{run_id}/stream` 推流 | 启动与推流分离，便于幂等 |

#### 7.2.3 前端消费：**不引 SDK**

用原生 `EventSource`（约 60 行）消费即可；类型定义放 `frontend/src/types/agui.ts`。
**先不引 `@ag-ui/client` / CopilotKit**（§6.1 拍板）。

### 7.3 取消机制（地基第三块）

#### 7.3.1 时序

| 步 | 动作 | 对应契约 |
| --- | --- | --- |
| 1 | 前端点「取消」→ `POST /api/research/{run_id}/cancel` | — |
| 2 | 后端 `cancel_events[run_id].set()`，**立即返回**（< 10 ms） | — |
| 3 | 前端**立即**显示「正在停止」——**不等**后端确认完成 | **C1** |
| 4 | `iter_run` 循环：yield 完当前 step → `should_cancel()` 为真 → `break` | **C2** 不再启动下一节点 |
| 5 | 当前节点已自然结束（检查点在 `yield` **之后**） | **C3** |
| 6 | 不再产生新的 LLM 调用（不再驱动 graph） | **C5** |
| 7 | 发 `RUN_FINISHED(cancelled=True)`，流程停止 | **C4** |
| 8 | 前端展示半程结果 | — |

#### 7.3.2 最坏等待（C6）

= **当前节点剩余执行时间**。实测估算：**中位 14.4 s / p90 17.7 s / 最大 31.4 s**（§5.4.1，62 题）。
⚠️ 该值为均值估算的**乐观下界**，实施时补节点级时间戳后校准。

#### 7.3.3 🚨 关键设计：取消**不得**走 `_recover_from_exception`

这是整个 W9 最容易踩、后果最严重的一处。理由：

1. `RUN_STATUSES = (success, degraded, failed)`（`state.py:22`）**只有三态**，没有 `cancelled`
   ⇒ 取消**不应引入第四态**，否则破坏封闭性、影响 eval 统计。
2. `_recover_from_exception` 会 `set_error()`（写 `run_status = FAILED`）+ `add_degradation()`
   ⇒ **用户主动取消会被记成系统故障**，直接污染 W8 的 **故障可归因率 100%** 这条 A 类验收。
3. 取消是**正常终止**，不是故障。

⇒ **取消路径**：`break` + `RUN_FINISHED(cancelled=True)`；
**不写** `state.run_status`、**不写** `degradation_log`、**不调** `set_error`、
**不调** `_recover_from_exception`。

`_recover_from_exception` **仅**用于真实异常路径（§7.1.4），从而保住：
`run_status` 三态封闭 ✓ ｜ W8 故障可归因率 ✓ ｜ eval 侧零影响（`run.py` 从不取消）✓。

#### 7.3.4 半程结果从哪来

`break` 时手上已持有**最后一个完整 state 快照**（`values` 模式）⇒ 直接展示，
**无需**从 `MemorySaver` checkpoint 恢复。报告字段若为空则前端显示「已取消，无报告」。

---

### 7.4 最小骨架（实施顺序第 4 步）—— ✅ 已落地

#### 7.4.1 目录

```
web/
├── backend/
│   ├── main.py        # FastAPI 装配 + 4 条路由（health / start / cancel / stream）
│   ├── agui.py        # AG-UI 事件名常量 + SSE 帧序列化 + 心跳帧
│   └── runner.py      # RunManager：线程驱动 iter_run()，产出 SSE 帧 + 取消 + 重连回放
└── frontend/
    ├── package.json / vite.config.ts / tsconfig.json / index.html
    └── src/
        ├── main.tsx
        ├── App.tsx                    # **刻意无视觉**：只做「事件流水 + 取消按钮」
        ├── hooks/useResearchStream.ts # 原生 EventSource 消费 SSE
        └── types/agui.ts              # AG-UI 事件类型
```

⚠️ `web/app.py`（旧 Streamlit）**暂未删除** —— 按 D-20「迁移完成后直接替换」，
待前端覆盖完成标准 7 项后一次性删除，避免中间态双维护。

#### 7.4.2 关键实现决策

| 决策 | 理由 |
| --- | --- |
| **`iter_run()` 放工作线程** | `graph.stream()` 是**同步**生成器，直接在协程里跑会阻塞事件循环 ⇒ 线程 + `queue.Queue` 回传帧 |
| **心跳用 SSE 注释 `: ping`** | 不是事件 ⇒ 不触发前端 `onmessage`，无需前端过滤 |
| **响应头加 `X-Accel-Buffering: no`** | 关掉 nginx 缓冲，否则长连接的帧会被攒着不推 |
| **重连回放 `replay(run_id, after=N)`** | 帧序号即列表下标 ⇒ 切片即可，防 `EventSource` 自动重连**重跑整个研究** |
| **前端不带 Tailwind（骨架阶段）** | 实施顺序第 4 步不做视觉；Tailwind 留在第 5 步 |
| **`package-lock.json` 入库** | 供应链可复现；`.gitignore` 已显式忽略 `node_modules/` 与 `dist/` |

#### 7.4.3 落地验证（2026-09-20 实测）

| 项 | 结果 |
| --- | --- |
| `fastapi` 实际安装 | `fastapi==0.141.1` + `annotated-doc==0.0.5`，**与 §5.1 预检完全一致**，现有锁定包零变动 |
| `values` 首帧语义 | ✅ **已证实**：首帧是初始 state（`progress_len=0`）⇒ `iter_run()` 跳过它 |
| 端到端冒烟（假 graph，零 LLM） | 帧序列 `RUN_STARTED → (STEP_FINISHED+STATE_DELTA)×N → RUN_FINISHED` ✅ |
| 取消冒烟 | 6 步任务中第 3 步取消 ⇒ 停在 3 步、`cancelled=true`、**无 RUN_ERROR**、`run_status` 仍 `success` ✅ |
| 契约测试 | `tests/test_web_streaming.py` **15 条** + `tests/test_web_api.py` **8 条** 全绿；另覆盖终局结果字段与 `Last-Event-ID` 重连 |
| 前端 | `tsc -b` 通过；`vite build` 成功（283 modules / 330.4 KB JS / gzip 103.1 KB） |

#### 7.4.4 启动方式

```bash
# 后端
python -m uvicorn web.backend.main:app --port 8000
# 前端（Vite dev server 已配 /api 反代到 8000）
cd web/frontend && npm run dev
```

#### 7.5 视觉与交互实现（2026-09-21）

- `web/frontend/src/index.css` 建立深色研究工作台主题、卡片/表单/按钮规范、Markdown 报告排版、表格/代码/引用样式，并保留 `prefers-reduced-motion` 兼容。
- `App.tsx` 拆出输入、边界说明、阶段进度、实时事件、降级面板、报告、引用校验、来源、反思轨迹与 validator 统计等可审计区块；桌面双栏、窄屏单栏。
- 前端只消费事件与终局结果，不复制 `research_engine` 判定逻辑；费用字段缺失时不估算，文档摄取字段缺失时不伪造进度。
- 报告用 `react-markdown` + `remark-gfm` 安全渲染，不使用 `dangerouslySetInnerHTML`；支持复制报告与下载 `.md`。

#### 7.6 取消与 SSE 端到端验收

| 验收项 | 实现/证据 |
| --- | --- |
| C1 | 点击后前端立即进入 `stopping`，按钮显示“正在安全停止” |
| C2~C5 | `RunManager` 在节点边界检查取消标志；不启动下一节点、不产生 `RUN_ERROR`、不走 `_recover_from_exception()` |
| C6~C7 | 保持同步节点自然结束；节点级实测最大约 31 s，当前不拆节点 |
| SSE 心跳 | 每 15 s 发注释帧 `: ping`，不触发业务事件 |
| 自动重连 | 每个连接按独立游标回放 `_frames`；支持 `Last-Event-ID` header/query，避免重跑研究或争抢事件帧 |

#### 7.7 测试与验收策略

- 后端契约测试位于 `tests/test_web_streaming.py` 与 `tests/test_web_api.py`，使用假 graph，零 LLM、零 API key、零外部服务。
- 当前验证：**23 条全绿**（15 条流式契约 + 8 条 HTTP 层）；覆盖正常完成、降级实时推送、取消、SSE 格式、心跳、结果字段和断点重连。
- 前端验证：`npm run build` 同时执行 TypeScript `tsc -b` 与 Vite production build；演示模式 `DR_DEMO=1` 已实跑提交、实时进度、降级、报告、引用、来源、反思轨迹与最终状态。

#### 7.8 测试与 CI 收口（2026-09-21）

##### 7.8.1 边界守卫（D-20 硬边界 ①）：前端不得 import `research_engine`

- 落地脚本 `tools/check_frontend_boundary.py`：递归扫描 `web/frontend/`（跳过 `node_modules/dist/.vite`）下所有 `.ts/.tsx/.js/.jsx/.mjs/.cjs`，凡出现字面量 `research_engine` 即 `exit 1`。
- 判据**只查字面量**：合法前端代码本就不该出现这四个字符，字面量匹配零依赖、可离线跑。
- 当前实测：**未引用**（前端只消费 SSE 事件与终局结果，判定逻辑全在 `web/backend/`）。

##### 7.8.2 CI 新增前端 job

`.github/workflows/ci.yml` 新增 `frontend` job（与 `lint-and-test` 并行）：

| 步骤 | 命令 | 目的 |
| --- | --- | --- |
| setup-node | `actions/setup-node@v4` + `node-version: "22"` + `cache: npm`（lock 路径 `web/frontend/package-lock.json`） | 项目用 Node 22；`package-lock.json` 已入库 ⇒ `npm ci` 可复现 |
| 装依赖 | `npm ci` | 按 lock 安装，供应链一致 |
| 类型检查 | `npx tsc --noEmit` | 抓类型回归 |
| 生产构建 | `npm run build` | Vite 构建 + 顺带 `tsc -b`；验证 `dist/` 可产、FastAPI 能托管 |

边界守卫挂在 `lint-and-test` job（Python 上下文，与 Arm 7 白名单检查同处），任一 Python 档失败都看得到：`python tools/check_frontend_boundary.py`。

##### 7.8.3 落地验证

| 项 | 结果 |
| --- | --- |
| 边界守卫本地跑 | `[guard] OK：前端目录未引用 research_engine`，`exit 0` |
| 守卫 ruff | All checks passed |
| 全仓 ruff | All checks passed（新增脚本零告警） |
| 全仓 MD 表格 | 0 不一致（含本文件 21 个表格块） |
| Web/SSE 测试 | **23 条全绿**（流式 15 + HTTP 8，零 LLM 零 key） |
| 前端实测（主理人） | `npm run build` 通过；`DR_DEMO=1` 实跑完整流程（提交 / 进度 / 降级 / 报告 / 引用 / 来源 / 反思 / 终局） |
