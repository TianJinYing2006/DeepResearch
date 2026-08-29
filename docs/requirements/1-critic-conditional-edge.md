# 需求-1-critic-conditional-edge

> 飞书镜像：DeepResearch 需求文档 / 需求-1-critic-conditional-edge
> 状态流转：草稿 → 进行中 → 自测 → 待合 → 已合

## 1. 元信息
| 项 | 值 |
|---|---|
| 编号 | #1 |
| 标题 | Critic 节点化 + conditional_edge 暴露反思循环 |
| 优先级 | P0 |
| 状态 | 草稿 |
| 负责人 | TianJinYing2006 |
| 关联 Issue | #1 |
| 关联 PR |  |
| 创建 / 更新 | 2026-08-30 |

## 2. 问题背景
需求文档（v1.0）点名「框架只调一下 / 换汤不换药」反模式。实测 `research_engine/graph.py` 是**纯线性 DAG**（`plan→research→write→validate→END`），无任何 `conditional_edge`；反思/停止判断（"信息充分度判断器"）藏在 `research_engine/agents/researcher.py` 的 `research_subquestion()` 的 `while` 循环里（`_judge_sufficiency`，L75）。面试官问"决定 continue/stop 的 conditional_edge 在哪"——现在拿不出。这是简历 agentic vs 编排 叙事的硬伤。

## 3. 需求分析
- 目标：把隐藏循环重构为图中**显式 Critic 节点 + LangGraph conditional_edge**，决策 `continue / revise / stop`。
- 成功定义：① `graph.get_graph()` 可视化含条件边；② 单次 research 能看到多轮 continue/revise 日志；③ 带 `max_depth` + `token_budget` 兜底终止，绝不无限循环。

## 4. 当前设计
- `research_engine/graph.py:L32-46`：`StateGraph(ResearchState)` 仅 `add_edge` 线性串 4 节点，无 `add_conditional_edges`。
- `research_engine/agents/researcher.py:L86-117`：`research_subquestion` 内部 `while depth < max_depth` 自决下一跳；充分度判断真实存在但不可见于图。
- `research_engine/state.py`：`ResearchState` 缺 `reflection_log / depth / token_used` 字段。

## 5. 优化方案
- 新增 `research_engine/agents/critic.py`：承接 `_judge_sufficiency` 逻辑，输出 `{decision: continue|revise|stop, reason, next_queries, add_subquestions}`。
- `research` 节点改为**单跳**检索（一次查询），不再内部 `while`。
- 图改为：`plan → research → critic →(conditional_edge)→ continue: research / revise: plan / stop: write → validate → END`。
- `ResearchState` 增 `reflection_log: List`、`depth: int`、`token_used: int`。
- `config.py` `ResearchConfig` 增 `token_budget`。

## 6. 设计策略
- 保留现有博查 / Qdrant 检索能力不动，只把"循环控制"提到图层级（最小改动、低风险）。
- 用 LangGraph `add_conditional_edges` + `checkpointer`（MemorySaver 起步）满足需求文档 D2「真用核心抽象」。
- revise 分支回 `plan` 以体现"计划动态修订"（FR2.2）。

## 7. 验收标准（DoD）
- [ ] `graph.get_graph()` 含 critic→research / critic→plan / critic→write 条件边
- [ ] CLI 跑通带多轮 continue/revise 日志
- [ ] `max_depth` / `token_budget` 兜底必达 stop，无死循环
- [ ] 现有检索结果质量不下降（对比改造前后样例）

## 8. 影响范围与风险
- 动：`graph.py`、`researcher.py`、`critic.py`(新)、`state.py`、`config.py`。
- 风险：循环提到图后状态传递需正确（findings 累积、去重）；回归检测用改造前后同 query 对比。

## 9. 测试策略
- 单测：`tests/` 增 `test_graph_loop.py`，断言条件边路由与兜底终止。
- 集成：CLI 跑 1 条简单 + 1 条难问题，肉眼核对反思日志。
- eval：跑现有 retrieval/citation eval，引用准确率不劣于基线。

## 10. 变更记录
| 日期 | 类型 | 原因 | 改动摘要 | 关联 PR/commit |
|---|---|---|---|---|
| 2026-08-30 | 优化 | 消除"框架只调一下"面试风险 | 建需求文档 + CONTRIBUTING 约束 | commit(dev 基线) |
