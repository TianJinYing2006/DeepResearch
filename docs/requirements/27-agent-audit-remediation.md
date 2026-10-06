# 需求 27：Agent 架构审查整改（F01–F15 回填 + P1 复核修复）

> 状态：**已合（PR #141 squash → dev `2e1eb82`，2026-10-07；CI 6/6 绿）**；实施进度以 `docs/project-status.md` 为唯一看板（D-01）。
> 来源：`local-artifacts/agent-review-2026-10-05.md`（外部审查 F01–F15）与
> `local-artifacts/agent-review-verification-2026-10-06.md`（复核报告 R01–R10，基线 `985d406`）。
> 飞书镜像：**已创建（2026-10-07）** —— docx token `DuTTdoYcgoQI3SxQt0wcWFSMnJe`｜
> 显示名「需求设计文档：Agent 审计整改」｜URL https://wcnnpvbxd7li.feishu.cn/docx/DuTTdoYcgoQI3SxQt0wcWFSMnJe
> （经导入接口创建；同文件夹 `MYR6fazL5la0ardJdUecOBkVnd8` 另有占位页
> `Wb3YdpvIoo5J4pxJfgFcCxgwnbc` 指向该镜像，待飞书客户端内整理）。
> 记录纪律：F01–F15 回填第一轮落地（PR #131–#139）；R01–R10 记录本轮 P1 修复与剩余 P2 边界。

## 1. 元信息

| 项 | 值 |
|---|---|
| 编号 | 27 |
| 标题 | Agent 架构审查整改（F01–F15 回填 + R01–R10 复核修复） |
| 优先级 | P1（事实正确性 / 报告完整性 / 费用与执行一致性） |
| 状态 | 已合（PR #141 → dev `2e1eb82`） |
| 负责人 | TianJinYing2006 |
| 关联 Issue | #140（F01–F15 第一轮为 PR #131–#139；本轮 P1 修复范围见 §5） |
| 关联 PR | #141 |
| 创建 / 更新 | 2026-10-07 |

## 2. 问题背景

外部审查（2026-10-05）给出 F01–F15 共 15 项架构与能力问题；第一轮整改以 PR
#131–#139 落地（Critic 证据输入、frontier 停止解耦、Worker 所有权、证据三层、
有界返工、预算准入、证据融合、多源印证、独立评测等）。复核报告（2026-10-06）
确认 F01–F15 主修复均已落地，但指出若干**边界缺口仍未闭合**（R01–R10），
其中 P1 项会导致：

- 同来源的不同论断继承他人裁决被判通过（R01，事实正确性）；
- 返工按不可靠的删除区间误删背景材料 / 留下孤立引用，且把校验未完成当"不忠实"删除（R02）；
- 无引用事实零成本绕过质量闭环（R03）；
- 原文溯源失败仍可用工作摘要判通过（R04）；
- 预算无预占可并发越闸，显式 timeout 可越过任务时限（R05）；
- owner 守卫与租约接管的写入竞争未串行化（R10）。

本轮按用户确认的范围先修 **R01–R05、R10（P1）**，并逐条落入本文件。

## 3. 需求分析

目标（可量化成功定义）：

1. 事实闭环：任一引用只接受"与自身论断对应"的裁决；数值/日期/否定词变化不得继承通过结论；
   无裁决 / origin 缺失 / 供应商失败一律进入 **UNKNOWN**，不判通过、不误删。
2. 返工安全：删除区间以"论断句"为单位且覆盖连续引用标记组；句子只要有一条引用通过即保留；
   UNKNOWN 不删除（渲染层显式标注）。
3. 事实覆盖：`事实句。[来源: 1]` 计为已引用；未引用事实句产出 span 明细并进入确定性降格；
   报告完成口径同时表达引用覆盖率与未引用事实数。
4. 原文溯源：声明的 origin 必须逐项解析；`missing` / `partial` 都拒绝用摘要做忠实度判决。
5. 费用与时限：每次外部 LLM 调用前按"输入估计 + 输出上限"原子预占；并发/重试不越预算；
   显式 timeout 也不得超过任务剩余时限。
6. 执行一致性：事件追加（含显式序号路径）先锁 runs 行校验 owner；续租携带 attempt。

## 4. 当前设计（复核时的问题点，基线 `985d406`）

| 编号 | 位置 | 问题 |
|---|---|---|
| R01 | `research_engine/agents/validator.py:611-652`（旧行号） | 无精确匹配时回退 `candidates[0]`；0.6 相似度无法识别数值/否定差异，且裁决可被复用 |
| R02 | `validator.py:260-334`、`repair.py:26-69` | `论断 [来源: 1][来源: 2]` 拆成两个区间；`论断。[来源: 1]` 触发 80 字符兜底吞前句；`verification_failed` 被当"不忠实"删除 |
| R03 | `validator.py:399-424`、`graph.py:443-488` | 未引用事实只进 stats；句末引用被句号切走导致覆盖率误计 0 |
| R04 | `evidence.py:106-140`、`validator.py:486-494/:704` | origin 部分缺失返回 `raw` 不计数；缺失仅回落摘要，faithful 仍判通过 |
| R05 | `budget.py:48-57`、`llm/client.py:70-101` | 无预占，3 并发可同时越过仅剩 1 token 的额度；`_resolve_timeout` 显式 timeout 直接返回，超时绕开 deadline |
| R10 | `web/backend/store.py:600-645/:755-765` | owner 版 INSERT 的 EXISTS 守卫未锁 runs 行；显式序号路径忽略 owner；`renew_lease` 只匹配 worker_id |

## 5. 优化方案（本轮 P1 实现）

### R01 · 裁决消费式对齐（事实正确性）

- `Validator._take_verdict`：每份 verdict **消费一次**（`consumed_verdict_ids`）。
  先按**规范化逐字一致**（claim / claim_echo 任一）匹配；未命中时仅当
  **数值签名一致 + 否定签名一致 + 相似度 ≥ 0.85** 才允许复用（容忍标点/空白差异）。
- 数值签名 = 阿拉伯数字与中文数词连写序列（`一百亿` vs `九百亿` 直接拒绝）；
  否定签名 = {不,未,无,没,非,勿,莫,禁} 集合。
- 无对应裁决 ⇒ `verified=False` + `verification_failed=True`（UNKNOWN），note 明示
  "不复用同来源其他论断的 verdict"；stats 增 `verdict_missing_for_citation_count`、
  `verdict_unused_count`。基线对照（`validator_fixes_enabled=False`）保留 v1.1 语义。

### R02 · claim 级返工边界 + 四态语义

- `_claim_text`：先剥离窗口收尾终止符（`。！？；\n`）再找内部句界 ——
  `论断。[来源: 1]` 不再切出空 claim / 触发 80 字符兜底误吞上一句。
- `_extract_citations`：相邻引用标记（中间仅空白）归并为一个 run，共享
  `claim_start` / `claim_end`；`[来源: 1][来源: 2]` 不再产生两个可独立删除的区间。
- `repair.plan_repairs`：仅删除"同区间全部**确证失败**（`verified=False` 且
  `verification_failed=False`）"的句子；含 UNKNOWN 的区间一律保留。
  stats 增 `kept_unknown`。

### R03 · 事实先于引用 + 未引用事实闭环

- `Validator._sentence_spans`：带偏移句子切分；句末仅隔分隔符/空白的
  `[来源: ...]` 归属该句（修复 `事实句。[来源: 1]` 覆盖率误计 0）。
- `_fact_coverage_stats`：新增 `uncited_fact_spans`（`{claim,start,end}`，上限 100 条）；
  `graph.route_repair` 在存在未引用事实时也触发返工。
- `repair.repair_report(report, citations, uncited_facts)`：对未引用事实做
  **确定性降格标注**（追加 `（未提供来源，未经核验）`，保留原句、幂等不重复）；
  stats 增 `downgraded_uncited`。
- `render.build_trust_statement(citations, state)`：可信声明追加"无引用事实句 N/M"
  口径（与引用口径分开计）。

### R04 · origin 链严格解析

- `resolve_evidence_text`：origin 逐项解析，状态扩展为
  `raw / truncated / missing / partial`；`missing`/`partial` 不影响文本回退，但
  `_build_findings_text` 通过 `statuses` 把状态传给 `validate()`。
- `validate()`：origin 为 `missing`/`partial` 的引用**不消费裁决、不判通过**，
  直接 UNKNOWN；stats 增 `evidence_partial_count`、`evidence_unknown_citation_count`。

### R05 · 预算预占与 deadline 收敛

- `budget.reserve_call / settle_call / add_charged_usage`：共享 `_BUDGET_LOCK`；
  预占 = 输入估计（CJK 1 token/字，其余 4 字符/token）+ 输出上限（调用方
  `max_tokens` 或 `config.llm.max_tokens`），立即计入 `state.token_used`；
  调用结束按真实 usage 结算（供应商缺 usage 时按预占**保守**计入）。
- `LLMClient.chat`：预占时把输出上限写入请求 `max_tokens`（实际消费 ≤ 预占）；
  失败调用按预占保守记账后原样抛出。
- `_resolve_timeout`：**始终**取 `min(显式或默认 timeout, 任务剩余时限)`；时限已过
  抛 `DeadlineExceeded`；任务时限存在时 `with_options(max_retries=0)` 禁止 SDK 内部
  重试叠加越过 deadline。
- 边界（未纳入本轮，见 §8）：搜索 / embedding 的货币账本、OpenAI SDK 之外的供应商
  重试、按 claim 检索长文窗口（当前仍是 4000 字符头尾保留）。

### R10 · 事件写入 owner 守卫串行化

- `RunStore._append_event_owned`：同一事务内先 `SELECT ... FOR UPDATE` 锁 runs 行，
  再校验 `worker_id + attempt + 执行态`，然后插入事件 —— 与 claim / sweep /
  finalize 的锁顺序一致（runs → run_events）。接管先提交 ⇒ 旧 attempt 被拒；
  本事务先持锁 ⇒ 事件排在接管之前。
- **显式序号路径同样校验 owner**（旧实现完全忽略 owner）。
- `renew_lease(..., attempt=...)`：续租凭据与 ownership 一致；`Worker._heartbeat_loop`
  传入本执行 attempt。
- `FakeStore` / `tests/test_worker.py` 同步。

## 6. 设计策略（取舍）

- **为什么"消费式对齐"而不是提高相似度阈值**：数值/日期/否定词差异在字符相似度上
  可能高达 0.85+；必须以签名做硬约束，且"一份裁决只服务一条引用"才能防一对多继承。
- **为什么 UNKNOWN 保留而不是删除**：供应商故障 / 预算耗尽 / origin 缺失不是"事实错误"；
  删除会误杀真实内容。保留 + 显式标注（note / 渲染）符合"可审计优先"。
- **为什么未引用事实用降格标注而不是删除**：删除会移除可能真实但暂无来源的内容；
  标注保留信息同时消除"无引用 = 免费通过"的激励。若后续引入补检索，可替换为补引用。
- **为什么预占缺 usage 时保守计入**：调用可能已消耗输入 token；释放会产生账面缺口。
- **锁顺序**：所有 run 级写入先锁 runs 行（或条件 UPDATE 自带行锁），再写 run_events，
  避免死锁；预占锁与 LLM 类级统计锁分离（无交叉等待）。

## 7. 验收标准（DoD）

- [x] R01：数值 / 否定词变化不继承裁决；重复论断只消费一次；相近回显仍可对齐（`tests/test_audit_p1_remediation.py`）
- [x] R02：相邻引用共享 span 且"一条通过整句保留"；句末引用 span 指向本句；UNKNOWN 不删除（同上）
- [x] R03：句末引用计为已引用；未引用事实产出 span、返工降格、渲染口径可见（同上）
- [x] R04：origin 缺失/部分缺失判 UNKNOWN，stats 显式计数（同上）
- [x] R05：并发不越预算、输出上限写入请求、缺 usage 保守计入、显式 timeout 服从 deadline（同上 + `test_budget_admission.py`）
- [x] R10：显式序号 + owner 在接管后被拒；续租 attempt 校验（同上；FakeStore 语义）
- [ ] R10 真实 PG 故障注入：两个连接 + 受控阻塞验证旧 attempt 事件/用量/终局均不越过接管边界（需 CI `infra` 环境）
- [ ] R04 长证据按 claim 检索原文窗口（当前 4000 字符头尾保留；承接 P2）
- [ ] R05 搜索 / embedding 货币账本与 attempt 级费用累计（承接 P2）
- [x] 定向回归全绿 + 全量失败集与基线逐项一致（27/27，见 §11）

## 8. 影响范围与风险

- 模块：`research_engine/{budget,evidence,repair,render,graph}.py`、
  `research_engine/agents/validator.py`、`research_engine/llm/client.py`、
  `web/backend/{store,worker}.py`、`tests/`。
- 行为变化：validator 判据更严格（无对应裁决 ⇒ UNKNOWN）；`max_tokens` 成为
  stateful 调用的默认输出上限（`config.llm.max_tokens`，可按环境调整）；
  修复关闭开关（基线对照）保留旧语义。
- 风险：相似度兜底阈值 0.85 + 签名约束可能把部分"改写型回显"判为 UNKNOWN
  （保守方向：不通过而不是误通过）；长报告若超过输出上限会被截断
  （`max_tokens` 可调，且属预算边界需要）。
- 兼容：`renew_lease` 的 `attempt` 为可选参数，旧调用方不受影响；
  `repair_report` 第三参数默认 None，旧调用方行为不变。

## 9. F01–F15 逐项落地回填（第一轮，PR #131–#139）

| 项 | 落地状态 | 实现摘要 | 关联 PR |
|---|---|---|---|
| F01 Critic 证据输入 | 主修复落地 | 证据清单按子问题组织（稳定 E 编号、限 20 条×200 字），裁决引用证据并经代码复核写回 state | #131 |
| F02 空 frontier | 主修复落地 | 移除"frontier 空"硬闸，队列空以 `no_new_queries` / `gap_reflection_cap` 收敛，单子问题可多跳 | #131 |
| F03 固定计算 | 主修复落地 | FLOPs 模板由 `CODE_EXEC_EVIDENCE_ENABLED` 门控（默认关闭：不执行、不入池） | #131 |
| F04 Worker fencing | 部分闭合 | `RunOwnership(worker_id, attempt)` 贯穿事件/用量/终局，租约丢失节点边界停止；R10 补并发串行化 | #132 / 本轮 |
| F05 工具上下文 | 主修复落地 | 每个工具线程独立 `copy_context()`，用量 sink 不再漏账 | #132 |
| F06 strict 用量 | 主修复落地 | `UsageSinkError` 不再被吞，各节点显式上抛 | #132 |
| F07 验证证据长度 | 部分闭合 | 500 字静默截断 → origin 原文 + 4000 字头尾显式截断；按 claim 窗口检索仍未建（§7） | #133 / 本轮 |
| F08 原文/摘要分层 | 部分闭合 | `findings`（append-only）/`working_findings`（摘要 + origin 链）分层；R04 补齐 origin 缺失语义 | #133 / 本轮 |
| F09 工具融合 | 主修复落地 | 来源多样性轮转入池（web/rag/arxiv×rank），adopted 实际采用数入 stats | #138 |
| F10 去重/新颖度 | 主修复落地，身份边界 | `(evidence_id, sq_id)` 去重 + 每跳新证据率；R09 来源身份待统一 | #138 |
| F11 事实质量闭环 | 部分闭合 | 一次确定性返工（span 级移除）；R01–R03 补齐裁决对齐、边界与未引用事实 | #134 / 本轮 |
| F12 多源复核 | 部分闭合 | support/contradiction IDs + 独立来源计数 + 三口径 stats；R06 真实独立性待补 | #139 |
| F13 调用预算/时限 | 部分闭合 | 耗尽拒绝、研究预留、默认 timeout、压缩组数上限；R05 补预占与 deadline 收敛 | #135 / 本轮 |
| F14 规格/计划身份 | 主修复落地 | `research_spec` + `plan_version` + replan `id_mapping` 双校验 + 旧证据重归属 | #139 |
| F15 独立评测 | 部分闭合 | 独立裁判 + coverage 完整性 + answer_integrity；R08 样本/存在性解耦待补 | #137 |

## 10. R01–R10 记录（本轮）

| 编号 | 优先级 | 状态 | 本轮处置 | 位置 |
|---|---|---|---|---|
| R01 同一来源 verdict 复用 | P1 | ✅ 已修复 | 消费式对齐 + 数值/否定签名 | `validator.py` `_take_verdict` |
| R02 修复区间不可靠 | P1 | ✅ 已修复 | claim 级 span + run 合并 + UNKNOWN 不删除 | `validator.py`、`repair.py` |
| R03 无引用事实绕过 | P1 | ✅ 已修复 | 句末引用归属 + `uncited_fact_spans` + 降格 + 渲染口径 | `validator.py`、`repair.py`、`graph.py`、`render.py` |
| R04 摘要判通过 | P1 | ✅ 已修复（窗口检索待补） | origin 逐项解析 + `missing/partial` ⇒ UNKNOWN | `evidence.py`、`validator.py` |
| R05 预算/时限 | P1 | ✅ 已修复（外部账本待补） | 预占/结算 + `max_tokens` + deadline 收窄 + 禁 SDK 重试 | `budget.py`、`llm/client.py` |
| R06 支持证据不在输入 | P2 | ⏳ 未修 | 与 R01 消费式对齐部分缓解；逐条 claim-support 关系与 canonical source 待做 | `validator.py` |
| R07 持久化证据链 | P2 | ⏳ 未修 | evidence manifest（版本化、正文快照引用）待做 | `web/backend/worker.py`、`evidence.py` |
| R08 独立裁判样本耦合 | P2 | ⏳ 未修 | 独立解析 claim 集与存在性、唯一性/齐备性校验待做 | `eval/citation_judge.py` |
| R09 知识库文档身份 | P2 | ⏳ 未修 | evidence_id 纳入 user/doc_id/chunk 作用域待做 | `evidence.py`、`context/manager.py` |
| R10 owner 守卫串行化 | P1 | ✅ 已修复（真实 PG 并发注入待验收） | `FOR UPDATE` 串行化 + 序号路径 owner + 续租 attempt | `store.py`、`worker.py` |

## 11. 测试策略与验证记录

- 新增 `tests/test_audit_p1_remediation.py`：**23 项**（R01–R05、R10 场景，全离线；
  含 3 线程并发预占、接管后显式序号拒绝等）。
- 定向回归（2026-10-07）：
  `test_critic_evidence / test_lease_ownership / test_usage_ledger / test_evidence_chain /
  test_repair_loop / test_budget_admission / test_eval_integrity / test_evidence_fusion /
  test_spec_and_crosscheck / test_audit_p1_remediation / test_validator_fixes /
  test_citation_audit` 合计 **144 项全绿**。
- 全量：**988 收集 = 911 通过 + 27 失败 + 50 跳过，0 errors**（396.6s）。
  27 项失败与复核基线**逐项一致**（web/RAG 依赖不可达导致的既有环境失败：
  `test_rag_api` 4 / `test_run_options` 2 / `test_upload_hardening` 3 /
  `test_web_api` 7 / `test_web_guardrails` 9 / `test_web_health` 2），无新增回归。
  结构化结果：`local-artifacts/verify-p1-final-20261007.xml`。
- 探针（`local-artifacts/test_review_985d406_probes.py`）翻转语义：**15 项通过**
  = 11 项修复验证 + 4 项未修 P2 仍可复现；`local-artifacts/verify-p1-probes-20261007.xml`。
- `ruff check`：改动文件全部通过。
- 未执行：真实模型质量评测、云上部署、真实 PG/Redis 故障注入（见 §7 未勾项）。

## 12. 后续批次需求清单（候选，含设计背景）

> 来源：2026-10-05 外部审查（F01–F15）、2026-10-06 复核报告（R01–R10 与「仍缺失的能力与
> 设计优化顺序」）以及本轮实现边界。按建议优先级排列；**状态 = 挂账**（未排期/未实现）。
> 「设计背景」= 当时做出该建议的分析依据（含已复现场景），便于下一批直接接续施工。

### 12.1 R06 · 支持证据必须来自实际裁判输入（P2）

- **设计背景**：复核已复现——报告只引用证据 1，证据 2 是另一来源的无关材料且被 trim 去掉，
  裁判仍返回 `supporting_evidence_ids=['1','2']`，代码判 `supported=True`、独立来源数 2；
  当前独立性是精确 source 字符串去重，同一论文的摘要/PDF、带参 URL、转载稿可伪装多源。
- **目标**：给裁判提供与 claim 相关的支持/反对候选证据；支持编号必须属于**实际提供且可解析**
  的集合；返回逐条 claim-support 关系；设 `canonical_source_id` / `origin_group_id`（URL 只是
  locator）；数量达标、来源独立、事实支持与冲突处理分别记录。
- **前置/依赖**：12.4（来源身份分组）。

### 12.2 R07 · 持久化 evidence manifest（P2）

- **设计背景**：终局 result 保存数字 finding_id、引用与原文轻量索引，但未保存
  `working_findings` 与「编号 N → 工作证据 → origin IDs」映射；压缩改变列表长度与顺序后，
  数字引用不能按原文列表位置反推；hash 只能验证拿到的内容，不能恢复没保存的内容。
- **目标**：版本化 evidence manifest（工作编号、稳定 ID、origin IDs、正文快照引用、文档版本、
  locator、claim span、修复前后记录、规格版本）；快照遵循用户隔离与保留期，不必全部内嵌
  到 SSE 或用户下载文件。
- **前置/依赖**：12.4。

### 12.3 R08 · 独立裁判与主链路解耦（P2）

- **设计背景**：复核已复现——同一报告与证据不变，仅改变主链路 Citation 的 existence，
  独立裁判会从「零调用」变成「判通过」；样本取自 `state.citations`，主链路过滤掉的表格/句子
  无法被独立裁判检查；裁判返回重复 idx 时后项覆盖前项，异常后仍报 `llm_failed=False`。
- **目标**：独立解析冻结最终报告、独立生成 claim 集合、独立解析证据存在性；使用版本固定的
  评测适配器；裁判输出统一检查唯一性、齐备性、类型与异常；加入修复前/后质量、删除率、
  回答完整度指标，防止「删掉所有困难事实」提升引用分数。

### 12.4 R09 · 知识库文档身份纳入证据 ID（P2）

- **设计背景**：复核已复现——两个不同 `doc_id` 的 `report.pdf` 相同正文片段得到同一
  `evidence_id`，去重后只保留一个；压缩按 `(source, sq_id)` 分组，同名不同文档可能混在一个
  摘要并只保留首个文档定位。
- **目标**：正文 blob 的内容 hash 与证据**出现位置**分离（blob 可复用，证据身份 =
  用户作用域 + doc_id/版本 + chunk_id/span）；同一片段可有多个 provenance；来源展示名
  不承担身份职责。

### 12.5 R10-PG · 真实 PostgreSQL 并发注入验收（P1 收尾）

- **设计背景**：本轮已把 owner 守卫改为 `SELECT ... FOR UPDATE` 串行化、显式序号路径也校验
  owner、续租携带 attempt；但「旧 Worker 恢复后并发 append」的真实 PG 故障注入未做
  （复核报告明确列为待并发验证项）。
- **目标**：两个真实连接 + 受控阻塞，验证旧 attempt 的**事件、用量、终局**均不越过接管边界；
  纳入 CI `infra` lane（本地无 PG 时可跳过）。

### 12.6 F07-P2 · 长证据按 claim 检索原文窗口

- **设计背景**：>4000 字符仍使用头尾截断；截断标记解决了「静默」，但没有保证裁判看见 claim
  的支持片段或限定条件，仍可能误拒/误判。
- **目标**：按 claim 在原文中检索窗口并保存被验证的具体 span；必要上下文不可获得时标记为
  未完成（UNKNOWN），而不是用头尾截断内容做事实裁决。

### 12.7 F13-P2 · 外部调用统一准入与货币账本

- **设计背景**：复核指出——搜索与 embedding 的支出不在 token 闸/费用模型内；OpenAI SDK 内部
  重试与整场 deadline 不共享；节点限制调用次数，但没有输入体积与耗时边界；重试 attempt 的
  实际费用需要明确累计口径。
- **目标**：每次外部调用统一准入（预算预占、总费用、deadline、取消、租约有效性）；
  搜索/embedding 接入货币账本；重试 attempt 累计实际费用；给压缩/写作/校验加输入体积边界。

### 12.8 能力 4 · search → read → extract

- **设计背景**：web 路径仍只使用标题和搜索摘要（`research_engine/agents/researcher.py:147-152`）；
  工具结果里的「原文层」并不等于完整网页，研究深度与可核验性受限。
- **目标**：定向读取正文/论文（接入 `net/safe_fetch.py` 已有的安全抓取），保存支持 span 与
  时效信息，使「引用 → 原文片段」可直接复核。

### 12.9 能力 5 · 子问题覆盖与冲突矩阵

- **设计背景**：复核指出下一步决策主要依赖全局 `sufficient` 布尔值，缺少每子问题覆盖、
  来源多样性、有效新增率、反对证据与待澄清事项的结构化输入。
- **目标**：用覆盖矩阵 + 冲突矩阵决定「继续/换查询/停止」；`research_spec` 结构化时间范围、
  语言、排除项、交付格式，并生成最终验收结果（回答完整度与未覆盖项可见）。

### 12.10 能力 6 · 可跨进程恢复

- **设计背景**：图仍使用 `MemorySaver`（`research_engine/graph.py:160`）；进程重启后 Worker
  接管是**重跑**，不是节点继续执行。
- **目标**：持久化 checkpoint（含版本）；先明确外部调用幂等、预算累计与 evidence manifest
  版本，再区分「SSE 回放 / 重新执行 / 从节点恢复」三种语义。

### 12.11 能力 7 · 新基线评测与复杂 Agent

- **设计背景**：历史基线不能代表今天主链路质量；质量阈值目前「告警，不阻断」；复核建议先用
  冻结证据、人标样本与独立事实集合校准裁判，再做真实工具端到端评测。
- **目标**：报告质量、回答完整度、费用、耗时与故障恢复一起作为验收（不只看引用通过率）；
  完成新基线后再引入更复杂的 Agent 协作/长期记忆等能力。

## 13. 变更记录

| 日期 | 类型 | 原因 | 改动摘要 | 关联 PR/commit |
|---|---|---|---|---|
| 2026-10-07 | bug | 复核报告 R01–R05、R10（P1） | 裁决消费式对齐 / claim 级返工边界 / 未引用事实闭环 / origin 强校验 / 预算预占与 deadline / owner 守卫串行化；新增 23 项回归 + 探针翻转 + ADR-0011 | PR #141 / commit `0482b23` |
| 2026-10-07 | 文档 | 记录纪律（CONTRIBUTING §5） | 新建需求 27 专档：回填 F01–F15 第一轮落地 + 逐条记录 R01–R10 | PR #141 |
| 2026-10-07 | 同步 | 飞书镜像闭环 | 飞书「需求设计文档：Agent 审计整改」已创建（`DuTTdoYcgoQI3SxQt0wcWFSMnJe`）；导入接口建在个人空间，文件夹内 `Wb3YdpvIoo5J4pxJfgFcCxgwnbc` 为指向占位（应用缺 `docx:document.block:convert` 权限，无法直接覆盖文件夹文档，待客户端整理或开权限后重灌） | 本文档 |
| 2026-10-07 | 合并 | P1 整改交付 | PR #141 CI 6/6 绿后 squash 合入 dev（`2e1eb82`）；需求 27 状态置「已合」 | PR #141 / `2e1eb82` |
| 2026-10-07 | 文档 | 后续批次清单 | 新增 §12：R06–R09、R10-PG 验收、长证据窗口、外部账本与能力 4–7，共 11 项，逐条记录设计背景与目标 | PR #142 |
