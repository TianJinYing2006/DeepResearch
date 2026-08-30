# DeepResearch 启动周计划（基于实际代码现状修订版）

> 配套文档：`WeChatBot/docs/deepresearch-requirements.md`（v1.0 需求冻结版）
> 本文档目标：把需求文档的 DoD 映射到**当前 `D:\项目\DeepResearch` 真实代码状态**，给出按简历价值排序的周排期。
> 修订原因：需求文档标注"开发待启动"，但实际代码**已是一套可运行的线性 LangGraph 骨架**（博查搜索 + Qdrant RAG + 三层 Qwen + Streamlit UI + 4 节点）。本文档据此重排里程碑，聚焦"补 DoD 缺口"，而非从零起步。

---

## 0. 关键结论（先看这段）

你现在的项目**不是从零开始**，但存在一个对简历/面试致命的结构性问题：

- ✅ 已经做对的：多 Agent 编排（Planner/Researcher/Writer/Validator）、RAG 混合检索、引用校验、多跳检索循环、Streamlit UI、eval 脚手架。
- ❌ **致命缺口 1（最高优先级）**：`graph.py` 是**纯线性 DAG**（`plan→research→write→validate→END`），没有任何 `conditional_edge`。反思/停止判断（"信息充分度判断器"）藏在 `Researcher.research_subquestion()` 的 `while` 循环里。面试官一句"把决定 continue/stop 的 `conditional_edge` 给我看"——**现在拿不出来**。这正是需求文档点名的「框架只调一下 / 换汤不换药」反模式。
- ❌ **致命缺口 2**：Langfuse 完全没接（NFR1 可观测缺失）——而这是简历四项缺口里的"主流可观测工具链"。
- ❌ **缺口 3**：工具只有 网络搜索 + RAG，缺 arXiv/学术检索（FR3.3）和代码执行（FR3.4）。
- ❌ **缺口 4**：eval 有脚手架但**没有 ≥20 条标注集 + 量化指标表**（NFR2 / DoD）。
- ❌ **缺口 5**：strategic 模型是 qwen-plus（便宜档），需求要求"强推理模型"用于规划/裁决。

**排序原则**：先解决"面试一问就露"的结构性问题（Critic 节点化 + Langfuse），再做工具与评测，最后开源+博客。功能堆得多不如把这两条讲透。

---

## 1. 现状盘点：实际代码 vs 需求文档

| 维度 | 需求文档(v1.0) | 实际代码现状 | 缺口 |
|---|---|---|---|
| 框架 | LangGraph（state+conditional_edge+checkpointer） | LangGraph 仅线性编排，**无 conditional_edge/checkpointer** | 🔴 决策循环未暴露到图 |
| 反思/停止 | Critic 节点 + continue/revise/stop | 逻辑在 `Researcher` 内部 `while`，无独立节点 | 🔴 同上 |
| 搜索工具 | Tavily | 博查 Bocha（可用，国内友好） | 🟢 接受，保留 |
| RAG | 未强调 | Qdrant 向量+BM25 混合检索 ✅ | 🟢 超出预期 |
| 学术检索 | arXiv/Semantic Scholar | 无 | 🟡 FR3.3 缺失 |
| 代码执行 | Python sandbox | 无 | 🟡 FR3.4 缺失 |
| 可观测 | Langfuse | 无 | 🔴 NFR1 缺失 |
| 模型 | 强推理（规划/裁决） | qwen-turbo/plus | 🟡 strategic 需可切换强推理 |
| 评测 | ≥20 条 + 指标表 | 有脚手架，无数据集/数字 | 🟡 NFR2 缺失 |
| 开源+博客 | GitHub + ≥1 篇博客 | 无 | 🟡 M5 缺失 |

---

## 2. 决策定稿表（D1–D7：文档默认 / 实际 / 建议锁定）

| # | 决策项 | 文档默认 | 实际代码 | 建议锁定 | 理由 |
|---|---|---|---|---|---|
| D1 | 语言 | Python 3.11+ | 3.10+ | **Python 3.11+** | 锁版本，保证可复现(NFR5) |
| D2 | 框架 | LangGraph | LangGraph(线性) | **LangGraph + 必须上 conditional_edge/checkpointer** | 现有投资保留，但补核心抽象 |
| D3 | 模型 | 强推理 | qwen-turbo/plus | **默认 Qwen，strategic 可切换 Qwen-Max/DeepSeek-R1** | 便宜模型跑通，难问题升强推理；简历话术="分层+按需升级" |
| D4 | 嵌入 | 随 D3 | text-embedding-v3 | **保留 text-embedding-v3** | 与 RAG 一致 |
| D5 | 工具 | Tavily | 博查 + Qdrant | **保留博查+Qdrant，新增 arXiv + 代码执行** | 博查国内可用且已跑通；补学术/计算 |
| D6 | 可观测 | Langfuse | 无 | **Langfuse（必做）** | 简历四项缺口之一 |
| D7 | 交付 | 开源+博客 | 无 | **GitHub 开源 + ≥1 篇博客** | DoD |

> 锁定的核心变化：**把"隐藏在 Researcher 里的循环"重构为图中真实的 Critic 节点 + conditional_edge**，并补 Langfuse。其余沿用现有实现，不推倒重来。

---

## 3. 优先级排序逻辑

1. **P0 — Critic 节点化（W1）**：直接消除"框架只调一下"风险，是 agentic vs 编排 叙事的硬证据。改动集中在 `graph.py` + 抽出 `agents/critic.py`，不动现有检索能力。
2. **P0 — Langfuse（W2）**：简历可观测缺口，接入成本低（OTel/装饰器包裹节点），收益高。
3. **P1 — 工具集齐（W3）**：arXiv + 代码执行 + strategic 强推理切换，让"工具自决"更完整。
4. **P1 — eval 数据集与指标（W4）**：把"效果不错"变成数字，DoD 硬性要求。
5. **P2 — 开源+博客（W5）**：收尾与对外佐证。

---

## 4. 周排期

### W1 — 暴露 Critic 为显式节点 + conditional_edge（核心 agentic 改造）
- **任务**
  1. 新增 `research_engine/agents/critic.py`：把 `Researcher._judge_sufficiency` 的判断逻辑迁出来，输出 `{decision: continue|revise|stop, reason, next_queries, add_subquestions}`。
  2. 重构 `graph.py`：
     - `research` 节点改为**单跳**检索（一次查询），不再内部 `while`。
     - 图变为 `plan → research → critic →(conditional_edge)→ continue: research / revise: plan / stop: write → validate → END`。
     - 加 `max_depth` / `token_budget` 兜底终止（兜底也走 `stop`）。
  3. `ResearchState` 增加 `reflection_log: List[...]`、`depth: int`、`token_used: int`（FR6/FR4.3）。
- **交付物**：图真正用 `conditional_edge`；CLI 跑通带反思循环；Web UI 能展示反思日志。
- **DoD 检查**：`python cli.py "..."` 能看到多轮 continue/revise；`graph.get_graph()` 可视化含条件边。

> 改造前后图对比：
> ```
> 改造前（线性）:  plan → research(内部while) → write → validate
> 改造后（agentic）: plan ⇄ research ⇄ critic
>                        │        │
>                     revise     continue
>                        │        └──(loop until stop)
>                        └──────────────► write → validate
> ```

### W2 — Langfuse 接入（NFR1）
- **任务**
  1. `requirements.txt` 加 `langfuse`；新增 `research_engine/observability.py` 用 `@observe` 包裹 4 个节点 + Critic。
  2. `config.py` 加 `langfuse` 段（public_key/secret_key/host），`.env.example` 补 `LANGFUSE_*`。
  3. 单次 research 全链路 trace（每步 input/output/token/工具调用）可在 Langfuse 回放。
- **交付物**：一次 research 的 trace 截图/录屏可用于博客与面试演示。
- **DoD 检查**：Langfuse 控制台能看到完整 span 树。

### W3 — 工具集齐 + 强推理切换（FR3.3 / FR3.4 / D3）
- **任务**
  1. 新增 `research_engine/search/arxiv.py`：arXiv/Semantic Scholar 学术检索，接入 Critic 工具调度。
  2. 新增 `research_engine/tools/code_exec.py`：Python sandbox（如 `exec` 沙箱 / 受限子进程）执行计算型子问题。
  3. `config.py` 的 `strategic_model` 支持切换 `qwen-max` / `deepseek-r1`；难问题走强推理。
- **交付物**：Critic 能在 web/RAG/arXiv/CodeExec 间自决；strategic 可配强推理。
- **DoD 检查**：一条需计算的学术问题能自动调 arXiv + 代码执行并溯源。

### W4 — eval 数据集 + 量化指标（NFR2 / DoD）
- **任务**
  1. 建 `research_engine/eval/dataset.jsonl`，≥20 条，覆盖：易/中/难 + 多轮追问 + 需计算 + 需学术检索。
  2. 扩展现有 `retrieval_eval/citation_eval/report_eval`，输出指标表：完成率 / 引用准确率 / 覆盖度 / token 成本 / 平均步数 / 反思有效性（该停时是否停）。
  3. LLM-as-judge + 人工抽检 20%，记到 `docs/eval-report.md`。
- **交付物**：可复跑的 eval 脚本 + 指标表 + 抽检记录。
- **DoD 检查**：能一条命令出指标表。

### W5 — 开源 + 博客（M5）
- **任务**
  1. GitHub 公开：清理密钥、补 `.env.example`、CI（lint/test）、LICENSE。
  2. README 重写：含运行步骤 + 架构图 + **面试话术**（agentic vs 编排边界、conditional_edge 怎么用）。
  3. 1~2 篇博客：① agentic loop 设计与 Critic 节点化踩坑；② Langfuse 接入实战。
  4. 简历回填（见 §7）。
- **DoD 检查**：仓库公开、README 可复现、博客发布。

---

## 5. eval 设计（W4 细化）

- **数据集（`dataset.jsonl`）字段**：`id, difficulty(易/中/难), type(单轮/多轮/计算/学术), query, followups?, expected_subquestions?, gold_sources?`
- **指标**
  | 指标 | 计算 | 目标 |
  |---|---|---|
  | 完成率 | 产出可用报告的比例 | ≥90% |
  | 引用准确率 | 结论能对应真实来源(LLM-judge+人工20%) | ≥85% |
  | 信息覆盖度 | 子问题被回答比例 | ≥90% |
  | Token 成本 | 平均/单次 | 记录基线，迭代降 |
  | 平均步数 | research→critic 循环次数均值 | 记录基线 |
  | 反思有效性 | 该停时停的比例(防无限循环) | 100% |
- **流程**：`python -m research_engine.eval.run --dataset dataset.jsonl` → 输出指标表 → 人工抽检 20% → 写入 `docs/eval-report.md`。

---

## 6. 简历回填口径（做完后，源自需求文档 §9）
- 技能栏：`Agent 框架：LangGraph（deepresearch：plan-act-reflect 循环 + conditional_edge 工具自决 + Langfuse 全链路 trace）`
- 项目栏：自主研究型 Agent、反思循环、工具自决、可观测(Langfuse)、量化评测(≥20 条)、开源。
- **只写真用且能展开的内容**（吸取 WeChatBot 误标教训）。

---

## 7. 风险与反模式复核（针对当前代码的具体雷点）
- 🔴 **换汤不换药**：当前图是线性 DAG——W1 必须把循环提到图层的 conditional_edge，否则仍是 DAG。
- 🔴 **框架只调一下**：LangGraph 必须用 state + conditional_edge + checkpointer，W1 验收"能展示条件边"。
- 🟡 **无 eval 数字**：W4 必须出指标表，否则"效果不错"无效。
- 🟡 **重新过度标注**：简历只写真用的（Langfuse 真接了才写，arXiv 真加了才写）。
- 🟡 **无限循环**：W1 必带 `max_depth` + `token_budget` 兜底。

---

## 8. 验收清单映射（需求文档 §1.2 DoD）
| DoD 项 | 对应周 | 状态 |
|---|---|---|
| 端到端结构化报告+溯源 | 已有，W1 后更稳 | 🟢 基础具备 |
| 模型自主决定检索策略与停止 | W1 | 🔴 待重构 |
| ≥1 反思/纠错循环 | W1 | 🔴 待暴露到图 |
| Langfuse 全链路 trace | W2 | 🔴 缺失 |
| ≥20 条评测 + 指标 | W4 | 🔴 缺失 |
| GitHub 开源 + README + 博客 | W5 | 🔴 缺失 |

---

> 下一步建议：从 **W1（Critic 节点化）** 开工。我可以直接帮你重构 `graph.py` + 新建 `agents/critic.py`，把隐藏循环提到图的 `conditional_edge`，并保留现有博查/RAG 检索能力不动。确认后我即开始。
