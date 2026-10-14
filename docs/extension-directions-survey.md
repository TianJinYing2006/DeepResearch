# 扩展方向调研：同类 DeepResearch 项目功能盘点与候选方向

> **性质**：调研文档（只读盘点 + 候选方向评估），**不是需求文档、不排期、不写代码**。
> **建立**：2026-10-13。触发：主理人提出「现在功能可能有些单一，看看同类项目都有哪些功能」。
> **纪律**：所有竞品功能均有公开出处；本项目的缺口按 README / `docs/project-status.md` 的**实测口径**陈述，不夸大、不粉饰。
> **排期影响**：本文件只出候选与成本账；真正排期须另开需求文档进 `docs/roadmap.md`（现批次 2 剩 R4 / R5）。

---

## 0. 结论先行

| 判断 | 说明 |
| --- | --- |
| **①「功能单一」的准确诊断** | 不是能力少，而是**交付形态单一 + 交互闭环缺失**：输入主题 → 一篇 Markdown → 结束。生产化（账号/配额/审核/队列/备份）同类项目**普遍没有**，我们反而超前。 |
| **② 真正缺的四件事** | 交互闭环（澄清 / 计划审批 / 追问）、交付形态（PDF·DOCX·PPT·图表）、数据源扩展（MCP 双向）、时间维度（定时·事件触发的持续追踪）。 |
| **③ 最大的单点机会** | **Human-in-the-Loop 闭环**。刚交付的 R3（跨进程 checkpoint，`thread_id=run_id`）天然支撑 LangGraph `interrupt` ⇒ 投入最小、面试讲述性最高、且是竞品共识方向（DeerFlow / open_deep_research / Gemini 三家都有）。 |
| **④ 反建议** | 不要为了「不单一」堆功能。**故障可归因 + 证据链可回溯**才是本项目真正的差异化（竞品普遍缺失），堆功能会稀释这个卖点。 |
| **⑤ 先还债再扩张** | 覆盖度 39.8%、代码沙箱不构成隔离（README §3.2 自陈），这两条是现存软肋，优先级高于任何新功能。 |

---

## 1. 同类项目功能盘点（均有公开出处）

### 1.1 开源框架

| 项目 | 架构 | 交互闭环 | 交付形态 | 数据源 / 工具 | 其他 |
| --- | --- | --- | --- | --- | --- |
| **GPT Researcher**<br>(assafelovic, ~29.9k★) | Planner → 并行 Executor（crawler/summarizer）→ Publisher；LangGraph 多智能体变体；**递归 Deep Research**（树状 depth/breadth，≈5 min / ≈$0.4） | ❌ 无审批门禁（Agentic Index 明确记「Human Oversight & Guardrails: Not documented」） | **MD / PDF / DOCX / JSON**；WebSocket 或 SSE 实时进度 | Tavily / Bing / Google / DuckDuckGo / Exa / arXiv / PubMed + **本地文档（PDF/Word/表格）**；JS 渲染抓取；图片抓取过滤 | **对外提供 MCP Server**（deep_research / quick_search / report writing / source tools）+ OpenAPI + A2A agent card；Ollama 本地模型；跨会话可选持久化 |
| **DeerFlow 2.0**<br>(字节, MIT, 2026-03 升 SuperAgent Harness) | Coordinator / Planner / Researcher / **Coder** / Reporter 五角色，LangGraph 状态图 + checkpoint | ✅ **人在回路**：自然语言交互式修改研究计划 + 自动接受；**报告后编辑**（Notion 式块编辑，tiptap，AI 润色 / 缩写 / 扩写） | **播客脚本 + TTS 音频**、**PPT**（marp-cli）、报告 | Tavily / Brave / DuckDuckGo / arXiv + Jina 爬取；**MCP 无缝集成**（私有域 / 知识图谱 / 网页浏览）；RAG 接 RAGFlow / VikingDB | litellm 100+ 模型；**Docker 隔离沙箱 + 持久文件系统 + Bash**（可装包、写文件、建 Web App） |
| **open_deep_research**<br>(LangChain) | 三阶段：**Scoping**（clarify + Research Brief 压缩）→ **Research**（Supervisor + 并行 Researcher 子 agent，上下文隔离）→ **Report**（**一次写成**，不并行写章节） | ✅ `clarify_with_user` 澄清提问（默认开，`max_clarify_turns`）；Workflow 架构支持 **human-in-the-loop 审批报告计划** | Markdown 报告 + 引用 | Tavily（默认）/ OpenAI native / Anthropic native / none；**MCP server 运行时注入 researcher 工具**；4 个模型角色分工 | Deep Research Bench（100 题 PhD 级，22 领域）RACE 评分；LangSmith 评测管线 |
| **STORM**<br>(Stanford OVAL, ~31k★) | 多视角提问 → 模拟专家访谈 → 大纲策展 → 维基式长文 | ❌ | 维基风格长文 + 引用 | Web + 检索 | 实测：组织度 +25%、覆盖广度 +10%（对比大纲驱动 RAG） |
| **PaperQA2**<br>(FutureHouse) | 面向科学文献的高精度 RAG 问答 / 综述 | ❌ | 答案 + 引用 | 论文语料 / 引文图 | FutureHouse Crow / Falcon / Owl / Robin 的底层引擎 |

### 1.2 商业产品（功能边界就是行业标杆）

| 产品 | 关键能力（2026） |
| --- | --- |
| **Gemini Deep Research**（2026-04，Gemini 3.1 Pro，Interactions API） | **协作规划** `collaborative_planning`：先返回计划 → 多轮修改 → 批准才执行；**远程 MCP 服务器**；文件上传 + 连接文件存储；**原生生成高质量图表与信息图**；实时流式；`background=true` 异步；分 **Deep Research**（交互向）/ **Deep Research Max**（异步向，追求全面）两档 |
| **ChatGPT Deep Research**（2026-02，GPT-5.2） | **全屏报告查看器**（左目录 / 右参考源，点击跳转）；**限定来源**（指定仅从特定网站或关联应用抓取）；**研究过程中实时干预**（改范围 / 补充资料）；导出 **Markdown / Word / PDF** |
| **Perplexity Computer**（2026-09） | **Automations**：定时 + **事件触发**（Slack / Gmail / Outlook / Linear / GitHub 连接器）；跨次运行保留历史；重要动作可设人工审核；等待触发不计费 |

### 1.3 横向规律（值得抄的共识）

1. **Scoping 是标配**：澄清提问 + 生成 Research Brief，几乎家家都有（open_deep_research / Gemini / ChatGPT 三种不同实现）。
2. **计划可见且可改**：Gemini 的协作规划、DeerFlow 的自然语言改计划、open_deep_research 的 plan 审批 —— 三家都把「执行前让人看一眼计划」做成产品原语。
3. **一次写成，不并行写章节**：LangChain 明确总结「并行写章节会重复、口径不一致，写作必须单点统一」—— 本项目 writer 已是单点，符合。
4. **MCP 是 2026 的通用扩展口**：GPT Researcher 出 MCP Server、DeerFlow 接 MCP、open_deep_research 注入 MCP、Gemini 连远程 MCP。四家全上。
5. **输出不止 Markdown**：PDF/DOCX（GPT Researcher、ChatGPT）、PPT + 播客（DeerFlow）、图表信息图（Gemini）。
6. **从一次性走向持续**：Perplexity Automations 是最新信号（定时 + 事件触发 + 跨次历史）。

---

## 2. 本项目的真实缺口（诚实对照）

| 维度 | 本项目现状 | 竞品普遍水平 | 缺口等级 |
| --- | --- | --- | --- |
| 交互闭环 | 提交 → 进度 → 取消 → 报告。**无澄清提问、无计划审批、无报告后编辑、无 follow-up 追问** | 澄清 + 计划审批是标配；DeerFlow 另有块编辑 | 🔴 大 |
| 交付形态 | `md` / `json` 导出 + 只读分享（`DR_SHARE_ENABLED`）+ 分段渲染 | PDF / DOCX / PPT / 图表 / 播客 | 🟡 中 |
| 数据源 | 博查·Tavily + arXiv + RAG(Qdrant+BM25) + 代码执行 | 上述 + **MCP（消费 & 对外）** + 本地多格式文档 + JS 渲染抓取 | 🔴 大 |
| 时间维度 | 一次性 run；有队列 Worker 但无定时 / 事件触发 | Perplexity Automations（定时 + 事件触发 + 跨次历史） | 🟡 中 |
| 记忆 | 无跨 run 用户级记忆 / 研究档案 | GPT Researcher 可选跨会话持久化；2026 记忆已成基座能力 | 🟡 中 |
| 架构 | 单图 8 节点串行（plan → research → critic → revise → write → validate → repair → render） | open_deep_research：Supervisor + 并行 Researcher 子图（**上下文隔离**） | 🟡 中（高风险改动） |
| **生产化** | **账号 / 配额三闸 / 多租户隔离 / 内容安全申诉 / 队列 Worker / 备份恢复演练 / OTel** | **普遍为零**（GPT Researcher 官方口径：鉴权、身份、治理「Not documented」） | 🟢 **我们的优势，不是缺口** |
| **故障可归因** | `failure_reason` 单点定义 + 三层状态分层 + 实测归因率 0%→100% | 无 | 🟢 **优势** |
| **证据链可回溯** | 快照内容寻址 + 逐字引文 + char offset + 零模型自检 + 分级处置（需求 34） | 无 | 🟢 **优势** |
| **自身软肋** | 覆盖度 **39.8%**；代码沙箱三层**不构成恶意代码隔离**（README §3.2） | — | 🔴 **先还债** |

---

## 3. 候选扩展方向（含成本账 / 时间账 / 面试价值）

> 工作量按本项目实际节奏估（一个需求 = 独立分支 + PR + 单测 + CI 全绿，通常 1~3 天/项）。

| # | 方向 | 竞品参照 | 工作量 | 依赖 | 面试讲述价值 | 风险 | 建议 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| **A** | **HITL 交互闭环**：① 提交后 LLM 澄清提问（≤2 轮）② plan 产出后 `interrupt` 停等人批准 / 改计划 ③ 报告后基于已有证据池 follow-up 追问（不重搜） | open_deep_research `clarify_with_user`；Gemini 协作规划；DeerFlow 改计划 | **3~5 天** | **R3 已交付的 PG checkpoint**（现成的 interrupt 载体）；③ 依赖 R2 evidence manifest | ⭐⭐⭐⭐⭐ `interrupt` + checkpoint + 状态恢复是 LangGraph 核心考点，且能讲「为什么要在 plan 后停」 | 低（不改核心判定口径） | **P0，下一个批次主线** |
| **B** | **MCP 双向**：① 消费外部 MCP 工具（注入 researcher）② 对外暴露 MCP Server（deep_research / quick_search / 报告导出） | GPT Researcher MCP Server；DeerFlow MCP；open_deep_research `MCPConfig`；Gemini 远程 MCP | **4~6 天** | 需 MCP SDK；② 需复用现有鉴权 / 配额 | ⭐⭐⭐⭐⭐ 与 WeChatBot（自研 MCP Server，Java/FastMCP）形成**跨生态呼应**：一边写 Server、一边消费 Server | 中（工具权限 / SSRF 边界要重新划） | **P1** |
| **C** | **交付形态**：PDF / DOCX / PPT 导出 + 报告内图表（Matplotlib/ECharts 渲染进报告） | GPT Researcher PDF/DOCX；DeerFlow PPT；Gemini 原生图表 | **3~5 天** | 已有 `export.py`；PPT 需 marp 或 python-pptx | ⭐⭐⭐ 工程向，能讲但不够「Agent」 | 低 | **P2**，最直观解决「功能单一」的体感 |
| **D** | **持续追踪（定时 / 事件触发）**：订阅一个主题，按 cron 重跑并**diff 上一轮 manifest**，只报新增 / 变化的证据 | Perplexity Computer Automations | **5~8 天** | 队列 Worker 已有；diff 依赖 R2 manifest | ⭐⭐⭐⭐ 从「一次性工具」变「持续助手」，叙事升级明显 | 中（成本不可控，需强预算闸） | **P3** |
| **E** | **跨 run 记忆 / 研究档案**：用户级研究偏好 + 历史结论回流为个人知识库，新 run 可复用 | GPT Researcher 可选持久化；2026 记忆基座化 | **3~5 天** | RAG 多租户作用域已有（P5-A `scope.py` 可直接复用） | ⭐⭐⭐ 中等 | 中（隐私与作用域要重划） | **P3**，可与 D 合并成一个「长期研究助手」批次 |
| **F** | **Supervisor + 并行研究子 agent**（`Send` 子图，上下文隔离） | open_deep_research multi-agent 架构 | **8~15 天** | **先做 R5 档位耗时标定**（roadmap 已有该项，正是它的前置） | ⭐⭐⭐⭐ 高，但需能讲清「为什么写作仍单点」 | 🔴 **高**：会改 graph 拓扑 ⇒ W8 基线可比性受损，故障归因要重新接线 | **P5，且必须 R5 之后**；优先只做「子问题级并行」而非全量 supervisor 化 |
| **G** | 播客 / TTS | DeerFlow | 2~3 天 | 需火山 TTS key | ⭐ 噱头 | 低但与主线无关 | ❌ **不做** |
| **H** | Computer Use / 浏览器自动化 | OpenAI Deep Research（RL browser） | 15 天+ | 沙箱安全债未还 | ⭐⭐ | 🔴 高 | ❌ **先还沙箱债**（README §3.2 已自陈三层不构成隔离） |
| **I** | 堆更多搜索源（PubMed / Exa / 语义学者） | GPT Researcher 7 个检索后端 | 1~2 天/源 | 无 | ⭐ 低 | 低 | ⚠️ **不解决真问题**：覆盖度 39.8% 是检索质量与判定口径问题，加源不改变 |

---

## 4. 建议顺序（填批次 2 之后的空白）

```
批次 2 剩余：R4（报告流式）/ R5（档位耗时标定）      ← 已在 roadmap，先做完
        │
        ├─ 批次 3（主线）：A — HITL 闭环（澄清 → 计划审批 → 追问）   3~5 天
        │       └─ 复用 R3 checkpoint，成本最低、叙事最强
        │
        ├─ 批次 4：B — MCP 双向                                    4~6 天
        │       └─ 与 WeChatBot 跨生态呼应；顺带把工具权限边界重划
        │
        ├─ 批次 5：C — 交付形态（PDF/DOCX/PPT + 图表）              3~5 天
        │       └─ 体感收益最大，解决「功能单一」的直观抱怨
        │
        └─ 批次 6：D+E 合并 — 持续追踪 + 研究档案记忆               8~12 天
                └─ 从「一次性研究工具」升级为「长期研究助手」

F（并行子 agent）单独立项，必须排在 R5 之后，且需重开评测基线讨论。
G / H / I 明确不做。
```

---

## 5. 一条必须说清楚的反建议

「功能单一」这个感受，**一半来自交付形态，一半来自叙事**。但本项目的护城河恰恰在**别人没有的地方**：

| 竞品公开口径（Agentic Index 对 GPT Researcher 的评估） | 本项目 |
| --- | --- |
| Human Oversight & Guardrails：**Not documented** | 三层状态分层 + 故障可归因率 0%→100%（16 条测试锁定） |
| Security, Identity & Governance：**Not documented** | 账号 / CSRF / 配额熔断 / 多租户 RAG 隔离 / 内容安全申诉 |
| Observability：Partial | OTel 全链路 trace + `gen_ai.*` + 成本估算 |

⇒ **建议**：每加一个功能，都问一句「它是否强化『可归因 / 可回溯 / 可审计』这条主线」。
-A（HITL）强化（人的审批本身是可审计事件）；
B（MCP）强化（工具调用纳入 `failure_reason` 单点定义）；
C（交付形态）中性；
G（播客）稀释。**用这个判据筛，就不会为了热闹而堆功能。**

---

## 6. 出处

| 来源 | 用途 |
| --- | --- |
| `https://github.com/assafelovic/gpt-researcher`（README / gptr.dev/llms-full.txt；Agentic Index 2026-09-21 评估） | GPT Researcher 功能与「鉴权/治理缺失」口径 |
| `https://github.com/bytedance/deer-flow`（README；DeerFlow 2.0 SuperAgent Harness 介绍） | 五角色架构 / HITL / 块编辑 / PPT / 播客 / MCP |
| `https://github.com/langchain-ai/open_deep_research`（README / DeepWiki） | Scoping-Brief / Supervisor+子 agent / MCP 注入 / Deep Research Bench |
| `https://ai.google.dev/gemini-api/docs/interactions/deep-research` | Gemini 协作规划 / MCP / 图表 / 两档 |
| The Decoder 转述（2026-02-11）OpenAI ChatGPT Deep Research 更新 | 全屏查看器 / 限定来源 / 实时干预 / 多格式导出 |
| Perplexity 官方博客（2026-09-29）Computer Automations | 定时 + 事件触发 + 跨次历史 |
| 本项目 `README.md` / `docs/project-status.md` / `docs/roadmap.md` | 现状与排期（唯一真相源，D-01） |
