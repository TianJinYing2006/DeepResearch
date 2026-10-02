# 需求 16：critic next_queries sq_id 引用完整性修复（报告空壳子问题 + 跳数帽稀释）

> 状态：**草稿**（随 PR 合入后按流程置「已合」）；实施进度以 `docs/project-status.md` 为唯一看板（D-01）。
> 来源：L3-A 内测实测（2026-09-30，run `86431ca78a6c` 标准档，「天蝎座和狮子座的恋爱适配度」）。

## 1. 元信息

| 项 | 值 |
|---|---|
| 编号 | 16 |
| 标题 | critic next_queries sq_id 引用完整性修复 |
| 优先级 | P0（用户可见的数据展示正确性） |
| 状态 | 草稿 |
| 负责人 | TianJinYing2006 |
| 关联 Issue | #75 |
| 关联 PR | 本 PR |
| 创建 / 更新 | 2026-09-30 |

## 2. 问题背景

标准档实测报告出现 4 个空壳章节：`## 子问题：q2.1 / q2.2 / q2.3 / q2a`（正文：「未在子问题
清单中明确定义其具体指向…」）；真实子问题只有 q1/q2。run 本身 SUCCEEDED
（302s / ¥0.466 / 7 跳 / 37/37 引用严格通过），属**标签链路缺陷**而非数据损坏。

## 3. 根因链（复核所得）

1. critic LLM 在 `next_queries` 中返回**不存在的 sq_id**（提示词只列子问题清单，未约束必须引用现有 ID）；
2. `research_engine/critic.py` 原实现 `state.next_queries = list(...)` **无校验**；
3. `research_engine/graph.py` 原样回填 frontier / 给 findings 打标；`per_subq_hop` 为幽灵 ID 各开新桶（**跳数帽被稀释**：本应第 6 跳收口，多跑了第 7 跳 ≈ +11s）；
4. `research_engine/context/manager.py` 渲染兜底 `sq_map.get(f.sq_id, f.sq_id)` 把原始 ID 当「子问题」标题喂给 writer。

## 4. 需求分析

目标：**任何来源的未知 sq_id 都不得进入 frontier 计数、报告章节或 citations 归属**；
不改引用校验（validator）口径；不可匹配时**不丢弃 query**（保检索覆盖）。

可量化：未知 ID 归一化率 100%（含审计事件）；`per_subq_hop` 仅含真实子问题 ID；
报告中不再出现非子问题 ID 的「子问题」标题。

## 5. 优化方案（业内调研后的选型）

行业做法与选型依据（调研 2026-09-30）：
- **源头约束**（OpenAI Structured Outputs `strict:true` 可保证 enum 不幻觉；JSONSchemaBench
  arXiv 2501.10868：约束解码有效但各家支持度差异大）——本栈 turbo/plus 混用且百炼 JSON Schema
  严格模式仅覆盖部分模型，暂不作为主方案，记为后续观察项（L1）；
- **收到即修复**（社区 structured-output-repair 的 fuzzy-key 递进修复思路）——**采用**：
  最长前缀归一化 `q2.1/q2a → q2`（L2）；
- **不可解析引用不得入图**（n8n 生态研究 arXiv 2606.29116：unresolved references 记 diagnostics
  并排除构建）——**采用**：无法匹配 → 置空归「未分类材料」，query 保留（L2）；
- **带可接受替代值的重试**（VeriHarness arXiv 2607.14167：反馈含「合法替代值」时成功率 +42~44pp）
  ——若线上未知 ID 仍高频再启用（动态 enum + 重试），记为后续（L1.5）。

三层防御落地：
- **L2（本 PR）**：`critic.normalize_next_queries()`——收到即修复 + 治理事件
  `next_query_sq_id_rewritten`（并入 `planner_events` 通道，不进 `degradation_log`、不推导 degraded）；
- **L3（本 PR）**：`ContextManager.format_for_writer()` 未知 sq_id 不再渲染成「子问题：<id>」，
  连续未知合并为「未分类材料」一组；
- **L1/L1.5**：观察后再定。

## 6. 设计策略

- 不改 validator / validator_stats 口径；不新增 state 顶层字段（复用治理事件通道）；
- 归一化为纯函数（`normalize_next_queries(queries, valid_ids)`），便于单测与复用；
- 事件命名与 Planner `duplicate_id_rewritten` 同构。

## 7. 验收标准（DoD）

- [x] 归一化单测：q2.1→q2 / q2a→q2 / 最长前缀（q12a→q12）/ 不可匹配→置空 / 审计事件 / drain 清空
- [x] `per_subq_hop` 不再新增幽灵桶（归一化后仅真实 ID 进入 frontier）
- [x] 未知 sq_id 不再出现在 writer 分节标题（契约单测）
- [ ] CI 全量 pytest 零回归（lint-and-test 三档 + infra）

## 8. 影响范围与风险

- 模块：`research_engine/critic.py`、`graph.py`、`state.py`（描述文案）、`context/manager.py`；
- 风险：极低——归一化只改 sq_id 标签，不改 query 内容与检索行为；若某子问题 ID 恰好是另一
  合法 ID 的前缀（q1 与 q12），最长前缀保证选择正确；
- 兜底：不可匹配置空 → findings 归「未分类材料」，不产生空壳「子问题」章节。

## 9. 测试策略

- 单测：`tests/test_critic_sqid.py`（4 条，注入 llm_fn 零 API）：
  前缀归一化与留痕 / 最长前缀 / 合法不打扰 / 渲染防御契约；
- 回归：`tests/test_critic_gap.py` + 全量 pytest；
- 线上验证（内测）：新标准档 run 中不再出现 `q2.1/q2a` 章节；治理事件可在 run 原始数据中审计。

## 10. 变更记录

| 日期 | 类型 | 原因 | 改动摘要 | 关联 PR/commit |
|---|---|---|---|---|
| 2026-09-30 | bug | 内测实测：报告空壳「子问题：q2.1/q2a」+ 跳数帽稀释（Issue #75） | L2 归一化 + L3 渲染防御 + 4 条单测 | 本 PR |
