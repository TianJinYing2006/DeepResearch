# ADR-0011：审计 P1 整改 —— 裁决消费式对齐、claim 级返工、origin 强校验、预算预占与 owner 串行化

## 基本信息

- **编号**：0011
- **标题**：审计 P1 整改（R01–R05、R10）：裁决对齐 / 返工边界 / 事实闭环 / origin 强校验 / 预算预占 / owner 串行化
- **日期**：2026-10-07
- **状态**：已采纳
- **涉及模块**：`research_engine/agents/validator.py`、`research_engine/{budget,evidence,repair,render,graph}.py`、`research_engine/llm/client.py`、`web/backend/{store,worker}.py`、`tests/`
- **关联需求**：`docs/requirements/27-agent-audit-remediation.md`（R01–R05、R10）

## 背景

2026-10-05 外部审查（F01–F15）后，PR #131–#139 完成第一轮主修复；2026-10-06 复核报告
（基线 `985d406`）确认主修复落地，但指出 6 项 P1 边界缺口：同来源裁决可被不同论断复用（R01）、
返工删除区间不可靠且把校验未完成当"不忠实"删除（R02）、无引用事实零成本绕过闭环（R03）、
origin 缺失仍可用摘要判通过（R04）、预算无预占且显式 timeout 绕开任务时限（R05）、
owner 守卫的 INSERT 与租约接管未串行化（R10）。

## 问题 / 动机

这些缺口都指向同一类风险：**"校验未完成"被误判为"事实错误"，或"已发生的消耗/写入"绕过硬边界**。
旧设计中最有代表性的三处：

1. `validate()` 在无精确匹配时回退 `candidates[0]`，仅用 0.6 字符相似度兜底 ——
   数值/否定词变化（一百→九百、未→已）无法被识别；
2. `repair.plan_repairs` 以 `(claim_start, claim_end)` 分区间，但该区间对连续引用与句末引用
   不可靠（孤立引用残留 / 误删上一句）；且 `verification_failed=True` 的引用也被删除；
3. `budget.admit_call` 只做一次性读检查，3 个并发调用可同时通过"仅剩 1 token"的准入。

## 方案

1. **R01 裁决消费式对齐**（`validator.py`）：`_take_verdict` 每份 verdict 只服务一条引用；
   先按规范化逐字一致（claim / claim_echo 任一）对齐，未命中才允许
   "数值签名一致 + 否定签名一致 + 相似度 ≥ 0.85"的复用；否则 UNKNOWN
   （`verification_failed=True`）。基线开关关闭时保留 v1.1 语义。
2. **R02 claim 级返工边界**：`_claim_text` 先剥离窗口收尾终止符再找句界；
   `_extract_citations` 把相邻标记归并为共享 span 的 run；`repair` 只删除
   "同 span 全部确证失败"的句子，UNKNOWN 一律保留（`kept_unknown` 入 stats）。
3. **R03 事实闭环**：`_sentence_spans` 让句末紧邻引用归属本句；`_fact_coverage_stats`
   产出 `uncited_fact_spans`；`route_repair` 对未引用事实也触发返工；
   `repair` 对未引用事实做确定性降格标注（保留原句）；渲染可信声明追加
   "无引用事实句 N/M"口径。
4. **R04 origin 强校验**：`resolve_evidence_text` 逐项解析 origin，状态扩展
   `raw/truncated/missing/partial`；缺失/部分缺失的引用在 `validate()` 中直接 UNKNOWN，
   不消费裁决、不允许用工作摘要作为事实依据。
5. **R05 预算预占与 deadline 收敛**：新增 `budget.reserve_call / settle_call`（共享锁），
   按"输入估计 + 输出上限"预占并写入请求 `max_tokens`，按真实 usage 结算
   （缺 usage 保守计入）；`_resolve_timeout` 始终取 `min(显式或默认, 剩余时限)`，
   任务时限生效时禁用 SDK 内部重试（`max_retries=0`）。
6. **R10 owner 串行化**：`store._append_event_owned` 先 `SELECT ... FOR UPDATE` 锁 runs 行
   再校验 `worker_id + attempt + 执行态`（显式序号路径同样校验）；`renew_lease` 支持
   `attempt` 且 Worker 心跳携带本执行 attempt。

## 理由与取舍

- **拒绝"提高相似度阈值"的简化方案**：数值/否定差异在字符级相似度上可能很高
  （0.85+），必须用签名硬约束；"消费一次"同时消除一对多继承。
- **UNKNOWN 保留、未引用事实标注而非删除**：供应商故障/预算耗尽/来源缺失不是事实错误；
  删除会误杀真实内容且不可恢复。保守方向是"不通过但不删除"，由渲染层显式暴露。
- **缺 usage 时按预占保守计入**：宁可账面偏保守，也不产生"实际花了钱、预算没扣"的缺口。
- **锁顺序 runs → run_events**：与 claim/sweep/finalize 一致；接管先提交则旧写入必被拒，
  本事务先持锁则事件排在接管之前，消除 READ COMMITTED 下的 EXISTS 快照窗口。
- **代价**：`max_tokens` 成为 stateful 调用默认输出上限（由 `config.llm.max_tokens` 控制，
  长报告可调大）；改写型回显可能被判 UNKNOWN（保守，不误通过）。

## 影响

- 行为：validator 更严格；返工删更少、标注更多；预算与时序硬边界生效；
  基线对照开关（`VALIDATOR_FIXES_ENABLED=false`）路径语义不变。
- 测试：新增 `tests/test_audit_p1_remediation.py`（23 项）；全量 988 收集 =
  911 通过 + 27 既有环境失败（与基线逐项一致）+ 50 跳过。
- 其余复核项（R06–R09、长证据窗口、搜索/embedding 货币账本、真实 PG 并发注入）
  仍按需求 27 §7/§10 挂账。

## 变更记录

| 日期 | 变更说明 |
|------|---------|
| 2026-10-07 | 初始记录：R01–R05、R10 六项 P1 整改与回归 |
