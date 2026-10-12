# 需求-35-batch2-recovery-and-manifest

> 状态流转：草稿 → 进行中 → 自测 → 待合 → 已合
> **来源**：`docs/roadmap.md` §3（批次 2：恢复与清单 + 体验）；挂账映射 **12.4**（R09）/ **12.2**（E7）/ **12.10**（E6）+ 体验①②。
> **前置决策（P0，已拍板）**：12.4 会改 RAG 证据身份/去重口径（现有基线未覆盖 RAG 变更，见 TBD-0）。
> 按 W8 纪律立项时先拍板 before 基线 —— **2026-10-12 拍板：不采全量 before 基线 + 确定性 RAG 回归夹具护栏**（记录见 §5 TBD-0）。
> 飞书镜像：https://wcnnpvbxd7li.feishu.cn/docx/C5afdrWMGoqhlux1FFUcsCyDntb（`C5afdrWMGoqhlux1FFUcsCyDntb`，
> 位于 `DeepResearch 需求文档` 文件夹，lark-cli 直建）。

## 1. 元信息
| 项 | 值 |
|---|---|
| 编号 | 35 |
| 标题 | 批次 2「恢复与清单」：来源身份分组（12.4）+ evidence manifest（E7）+ 跨进程恢复（E6）+ 体验①② |
| 优先级 | P1 |
| 状态 | 进行中 |
| 负责人 | TianJinYing2006 |
| 关联 Issue | #201 |
| 关联 PR | — |
| 创建 / 更新 | 2026-10-12 |
| 前置评审 | **TBD-0~7 全部已拍板（2026-10-12，按 v2 推荐）**；v2 已闭合设计评审 6 点（见 §11） |

## 2. 问题背景

- **12.4（R09，复核已复现）**：两个**不同 `doc_id`** 的 `report.pdf` 相同正文片段得到同一
  `evidence_id`（`evidence.py:45-47`：`sha256(source + "\\n" + content)[:16]`，而 RAG 的
  `source = "rag:<filename>"`（`researcher.py:199-213`）不含文档身份）⇒ `dedupe_new_findings`
  按 `(evidence_id, sq_id)` 去重（`evidence.py:76-110`）后**后到的同片段被误丢**；
  压缩按 `(source, sq_id)` 分组（`context/manager.py:46-48`）⇒ **同名不同文档混编**摘要，
  且只保留首个文档定位。另核实：检索结果已带 `generation`（`retriever.py:57`）且 `chunk_id`
  已内嵌版本（`rag_pipeline.py:81`：`{doc_id}:g{generation}:{index}`），但 **researcher 未把
  `generation` 存入 finding 元数据**（`researcher.py:273-278`）⇒ 分组/审计缺版本维度。
- **E7（12.2 R07）**：终局 payload 只存 `evidence_index`（原文层轻量索引：id/hash/locator/时间，
  **不含正文**，`worker.py:593` / `runner.py:748`）+ citations；**不存** `working_findings` 与
  「编号 N → 工作证据 → origin IDs」映射 ⇒ 压缩改变列表长度与顺序后，数字引用**不能按位置反推**；
  hash 只能验证拿到的内容，不能恢复没保存的内容。另两个缺口：① 未保存 **Writer 实际收到的
  有序工作证据清单**（每个 `Finding N` ↔ 稳定 ID / origin / 快照引用）——仅存 origin IDs 时，
  压缩规则或清单顺序变化后**编号不可重建**；② RAG 证据没有网页式 `snapshot_id`，且旧 generation
  会被清理（`cleanup_stale_generations` / 保留期）⇒ doc/version/locator + hash **不足以保证
  日后仍能取回原文**。
- **E6（12.10）**：图仍用 `MemorySaver`（`graph.py:165`，进程内）；进程重启后 Worker 租约接管是
  **重跑**，不是节点继续执行。前置：外部调用幂等（LLM 不可幂等）、预算累计口径、manifest 版本（E7）。
  预算账本细节：用量逐次进 `usage_ledger`（`client.py:142` `emit_usage` sink），但
  `state.token_used` 只在**节点完成时**回写（`store.py:526` `update_usage`）——节点中途崩溃时，
  崩溃前已发生的调用**不在最近 checkpoint**；重试若按 checkpoint 旧值写回会**低估累计消耗**
  （设计已承认调用会重复，但未定义 attempt 用量与恢复 state 的对账）。
- **体验①**：Writer 为单次 `smart_chat`（`writer.py:117`），**无 token 流**；SSE 只有节点级
  `STEP_FINISHED` 帧 ⇒ 长写作期间前端无正文增量。且 SSE 是 `run_events` 持久事件流按 `sequence`
  回放（`main.py:2460` `_stored_event_gen`，支持 `Last-Event-ID` 断线重连）——若已发出部分
  `report_delta` 后流式失败再回退非流式，前端会把完整报告**追加到半截文本后**（协议未定义）。
- **体验②**：quick/standard/deep 三档（`web/backend/profiles.py`）**耗时未标定**（无 p50/p90）
  ⇒ 「是否做子问题级并行（Send）」缺数据。

## 3. 需求分析

目标（一句话）：**证据身份与内容解耦、证据链可持久化复现、异常可续跑**（体验项为体感增量）。

- **R1（12.4）**：证据身份 = **版本化位置**：`scope + doc_id + chunk_id`（`chunk_id` 已内嵌
  generation：`{doc_id}:g{n}:{i}`，**验收锁该格式**；legacy 无 `chunk_id` ⇒ 回落内容寻址并登记）；
  **`content_hash` 不进身份**、仅承担完整性校验（身份/内容解耦）；`finding.metadata` 显式存
  `generation`；同一片段可有多个 provenance；**展示名不承担身份职责**。
- **R2（E7）**：**版本化 evidence manifest**，拆两个验收层：
  - **(a) 完整性可校验**（任意时点）：含 **Writer 实际收到的有序工作证据清单**（`Finding N` ↔
    稳定 ID / origin IDs / content_hash / 文档版本+locator / 快照引用）+ claim span +
    修复前后记录 + 规格版本 ⇒ 压缩/顺序变化后编号仍可重建、内容可核验；
  - **(b) 原文可重新取得**（保留期内）：web 经 `snapshot_id` 回查；RAG **限 generation 保留期内**
    经 doc/version/locator 回查（保留策略见 TBD-3）；超期后只保证 (a)。
  遵循用户隔离与保留期；**不必**全部内嵌到 SSE 或用户下载文件。
- **R3（E6）**：持久化 checkpoint（含版本）⇒ **从节点恢复**；**执行模式显式记录
  `resumed` / `replayed` / `fresh`**——「从节点续跑」才算恢复成功，无 checkpoint / 版本不兼容
  ⇒ 显式进入 `replayed`（降级路径，**不冒充恢复**）；**预算以 `usage_ledger` 为累计事实源**
  （恢复 state 与账本对账规则见 TBD-4）；三语义边界（回放/重跑/恢复）写入运维文档。
- **R4（体验①）**：Writer 报告流式产出（SSE 渐进正文）；**失败/重连协议必须闭合**：
  增量事件持久化粒度 + 失败时 `report_reset`（整段替换）语义，或仅允许在首个增量发出前降级
  （见 TBD-6）。
- **R5（体验②）**：档位耗时标定（各档 p50/p90）⇒ 结论**限定为「是否值得做并行实验」**；
  再做固定题集对照实验（并行 vs 非并行：完成时间 / 费用 / 质量）后才决策 Send（见 TBD-7）。

## 4. 当前设计（代码现状）

| 层 | 位置 | 现状 | 本批次处置 |
|---|---|---|---|
| 证据 ID | `evidence.py:45-47` | `sha256(source + 正文)` 前 16 位 | **R1**：身份要素按 source_type 分派（不含内容哈希） |
| RAG 身份 | `researcher.py:199-213` | `source="rag:<filename>"` + metadata `doc_id/chunk_id/locator` | **R1**：`doc_id` 进身份 |
| RAG 版本 | `retriever.py:57` / `rag_pipeline.py:81` | 结果带 `generation`；`chunk_id={doc_id}:g{n}:{i}`；**researcher 未存 generation** | **R1**：metadata 增 `generation`；身份用 `chunk_id`（格式锁定） |
| 去重 | `evidence.py:76-110` | `(evidence_id, sq_id)` | **R1**：id 本身含版本化位置（去重键不变） |
| 压缩分组 | `context/manager.py:46-48` | `(f.source, f.sq_id)` | **R1**：按身份键分组（rag 用 `(doc_id, generation, sq_id)`） |
| 终局 payload | `worker.py:593` / `runner.py:748` | `evidence_index`（轻量，无正文；**缺有序工作清单**） | **R2**：manifest 落盘（两验收层） |
| 用量账本 | `client.py:142` / `store.py:526` | 逐调用入 `usage_ledger`；`state.token_used` 仅**节点完成**回写 | **R3**：账本为累计事实源 + 对账规则 |
| checkpoint | `graph.py:165` | `MemorySaver`（进程内） | **R3**：PG checkpointer |
| 恢复语义 | Worker 租约接管 | **重跑**（无模式记录） | **R3**：`resumed`/`replayed` 显式落库；checkpoint 存在 ⇒ 续跑 |
| Writer | `writer.py:117` | 单次 `smart_chat` | **R4**：`chat_stream` + SSE `report_delta` |
| SSE 回放 | `main.py:2460` | `run_events` 按 `sequence` 回放 + `Last-Event-ID` | **R4**：delta 协议闭合（粒度 + `report_reset`） |
| 档位耗时 | `profiles.py`（三档） | 未标定 | **R5**：p50/p90 标定 + 实验决策框架 |

## 5. 优化方案（2026-10-12 全部拍板落档）

- **TBD-0（前置，P0）before 基线 —— ✅ 已拍板：不采全量 before 基线（2026-10-12）**：
  口径：**现有基线未覆盖 RAG 变更 ≠ R1 对输出无影响** ⇒ 以**确定性 RAG 回归夹具**（同名不同
  文档、不同 generation、同片段）作为口径护栏（进 R1 验收）；E7 为新增产物、E6 只影响异常路径、
  体验①② 为展示/标定。配对结论仍随 **12.11 新基线**（批次 2 后段）统一重建。
- **TBD-1（R1 身份设计）—— ✅ 已拍板（2026-10-12）：按推荐**：
  - `rag`：`ev_` + `sha256(scope | doc_id | chunk_id)[:16]`（**身份不含内容哈希**）；
    `chunk_id` 已内嵌 generation（`{doc_id}:g{n}:{i}`，**验收锁该格式**）；同一位置内容变化 ⇒
    身份不变、`content_hash` 变化（供复核）；legacy 无 `chunk_id` ⇒ 回落内容寻址并登记；
  - `web` / `arxiv`：**保持** `source + content`（URL / abs 即位置）；
  - `finding.metadata` 增 `generation`（显式存，供分组/审计）；
  - 「同一片段多 provenance」本批**只留接口**（`metadata["provenances"]` 预登记，不实现）。
- **TBD-2（R1 压缩分组）—— ✅ 已拍板（2026-10-12）：按推荐**：分组键改**版本化身份键**——
  `rag` 用 **`(doc_id, generation, sq_id)`**（generation 取自 finding 元数据；缺失回落 `chunk_id`
  解析），其余 `(source, sq_id)`（统一经 `evidence_group_key(f)` 帮助函数）。
- **TBD-3（R2 manifest 形态）—— ✅ 已拍板（2026-10-12）：按推荐**：
  `run_artifacts` 新 kind **`evidence_manifest`**（JSON；`manifest_version=1`；终局同事务写入），
  **不上新表**；内容含**有序工作证据清单**（`Finding N` ↔ 稳定 ID / origin IDs / content_hash /
  文档版本+locator / `snapshot_id`）+ claim span + 修复前后 + 规格版本；**两验收层**：
  完整性可校验（长期）/ 原文可取得（保留期内）；**RAG 原文保留策略**：随 generation 保留期
  （超期只保证可校验，manifest 显式标注来源层与可取得窗口）；快照引用沿用 `0022`。
- **TBD-4（R3 语义与幂等）—— ✅ 已拍板（2026-10-12）：按推荐**：本批**只实现「从节点恢复」**；
  幂等边界 = **节点边界**（恢复后重执行未完成节点，LLM 调用**可能重复**）；**预算对账**：
  `usage_ledger`（逐调用、含 attempt）为**累计费用事实源**——恢复时 state 预算以**账本汇总**
  初始化（或 `max(checkpoint.token_used, 账本汇总)` 取账本口径），终局成本/展示同口径；
  写回（`update_usage`）与账本的一致性校验入 stats；三语义 + 对账规则写入运维文档。
- **TBD-5（R3 存储选型）—— ✅ 已拍板（2026-10-12）：按推荐**：官方
  **`langgraph-checkpoint-postgres`**（`thread_id=run_id`）；依赖入 lock（**lock 重生成**）；
  checkpoint 带版本字段与兼容策略；**执行模式显式落库**（`resumed` / `replayed` / `fresh`，
  进 run_events/终局 payload）——checkpoint 存在且版本兼容 ⇒ `resumed`（从节点续跑）；
  缺失/不兼容 ⇒ `replayed`（降级重跑，**不算恢复成功**）。
- **TBD-6（R4 协议）—— ✅ 已拍板（2026-10-12）：按推荐 + 范围=并入本批**：
  `LLMClient.chat_stream`（DashScope OpenAI 兼容流式）+ Writer 增量发 SSE `report_delta`；
  ① 增量事件**持久化粒度**（如每 N 字符 / 每帧，进 run_events 以支持 `Last-Event-ID` 重连）；
  ② 失败语义——**已发出增量后失败 ⇒ 发 `report_reset` 并整段替换**（或仅允许首个增量发出前
  降级为纯非流式）；③ 测试覆盖**断线重连**与**半截输出后失败**；**并入本批（同需求跟踪）**。
- **TBD-7（R5 标定口径）—— ✅ 已拍板（2026-10-12）：按推荐**：用真实 run 数据
  （`runs.started_at/finished_at` + `request.profile`）统计各档 p50/p90；**结论限定为
  「是否值得做并行实验」**；后续用**固定题集对照实验**（并行 vs 非并行：完成时间 / 费用 / 质量）
  才做 Send 决策；**quick 档补测不能替代 standard/deep 数据**（样本不足时如实标注）。

## 6. 设计策略

- **身份与内容解耦**：blob 可复用、身份不可复用（12.4 核心；ADR-0011 精神延续）；
- **只增不改**：manifest 版本化；历史产物不回填（D-14 纪律：写侧只出新键 + 读侧 dual-read）；
- **账本为事实源**：任何恢复/重试路径的预算与成本以 `usage_ledger` 对账（不信任单一 state）；
- **异常路径专属**：E6 只影响「接管/重启」，正常路径零行为变化；执行模式显式可判定；
- **不新增外部服务**：checkpoint/manifest 全部复用 PG（与 N1 备份面一致）；
- **体验项独立小 PR**：R4/R5 不阻塞主线（R1→R2→R3）。

## 7. 验收标准（DoD）

- [x] TBD-0 决策：**不采全量 before 基线 + 确定性 RAG 回归夹具护栏**（2026-10-12 拍板；理由见 §5）
- [x] R1：✅ **已交付（2026-10-12）** —— 身份分派 `compute_finding_evidence_id`
      （rag=`sha256(scope|doc_id|chunk_id)`，**不含哈希**；web/arxiv/legacy 保持内容寻址）；
      `metadata` 增 `generation`；分组键 `evidence_group_key`（rag=`(doc_id, generation, sq_id)`）；
      **回归夹具** `tests/test_evidence_identity.py`（同名不同文档不误去重 / 不同 generation
      不同身份 / 同位置内容变化身份不变 / `chunk_id` 格式锁 / 压缩分组不混编）
- [x] R2：✅ **已交付（2026-10-12）** —— `research_engine/evidence_manifest.py`（`manifest_version=1`；
      **有序工作清单** N ↔ 稳定 ID/origin/hash/来源身份 + 引用 claim span + 修复前后 + 规格版本）；
      **两验收层**：完整性可校验（长期）/ 原文可取得（`retrieval.mode`：snapshot / rag_generation /
      via_origins / content_hash_only）；终局**同事务**落 `run_artifacts`（kind=`evidence_manifest`）
      + 并入导出（`export_json.result.evidence_manifest`）
- [x] R3：✅ **已交付（2026-10-13）** —— `langgraph-checkpoint-postgres`（`thread_id=run_id`；
      **库自管 schema**，Worker 首启幂等建表）+ 执行模式解析 `fresh`/`resumed`/`replayed`
      （checkpoint 版本比对；无 checkpoint/版本不兼容 ⇒ 显式降级含 `reason`）落 `RUN_STARTED`
      + 终局 payload；**账本对账**：`usage_ledger` 为累计事实源——`resumed` 经 `update_state`
      注入、终局 `max(state, 账本)` 口径（**预算不低估**，`replayed` 重跑路径同样同口径）；
      checkpoint 不可用 fail-silent 回落 MemorySaver；**kill→接管→续跑演练**（真 PG + 真 saver：
      resumed 不重放已完成节点/终局 120 不低估；replayed 显式标注/终局 130 同口径）；
      单测 8 + PG 契约 2 + 演练 2；运维语义见 `release-runbook.md` §7.2
- [ ] R4：Writer 流式（`chat_stream` + SSE `report_delta` + 前端）；**增量持久化粒度 +
      `report_reset` 失败协议**；断线重连 + 半截失败测试；失败降级不产生半截拼接
- [ ] R5：档位耗时 p50/p90 报告（结论 = 是否值得并行实验；quick 补测如实标注）+
      对照实验方案与 Send 决策记录
- [x] 测试：单测（身份/去重/分组/manifest 结构/checkpoint 往返）+ 真 PG 契约（manifest 写入、
      checkpoint 表）+ 全量套件绿 + 恢复演练（kill → 接管 → 续跑；
      **resumed 与 replayed 两路径分别断言**）✅ R1/R2/R3 交付即验证；演练见
      `tests/test_resume_drill_pg.py`（真 PG + 真 saver；CI infra lane）
- [ ] 文档：需求 27 §12.2/§12.4/§12.10 状态回填；口径断点登记（若动输出面）；
      README/运维文档同步（落地才写）

## 8. 影响范围与风险

- 动：`evidence.py`、`context/manager.py`、`agents/researcher.py`（R1）；`worker.py`/`runner.py`/
  导出（R2）；`graph.py` + worker 接管路径 + `requirements-lock`（R3）；`llm/client.py`/
  `writer.py`/`streaming.py`/前端（R4）。
- 风险①：身份变更影响历史去重行为 ⇒ **不回填历史**（新键只对新产物生效）；
- 风险②：checkpoint 依赖 LangGraph 版本 ⇒ 锁版本 + 兼容字段；升级演练登记；
- 风险③：恢复路径**重复计费**（LLM 不可幂等）⇒ 账本（`usage_ledger`）为事实源 + 对账规则，
  如实记账不静默；
- 风险④：流式涉及前端 ⇒ `check_frontend_boundary.py` 守卫不越界（N4 精神）；
- 兜底：R1~R3 全部 fail-open 到现状（身份退化为现状计算；checkpoint 不可用/版本不兼容 ⇒
  显式 `replayed` 重跑（**不算恢复成功**）；流式失败 ⇒ 非流式 + `report_reset` 协议）。

## 9. 测试策略

- 单测（离线）：身份分派（rag/web/arxiv）、同名不同文档不误去重、分组键（含 generation）、
  manifest 结构（版本/有序清单/字段齐备）、checkpoint 往返（stub）；
- 真 PG 契约（CI `infra` lane）：manifest 写入/读取、checkpoint 表读写与版本、账本对账；
- 恢复演练：本地 compose 中 kill worker → 接管 → 分别断言 `resumed`（从节点续跑、预算不低估、
  产物一致）与 `replayed`（无 checkpoint/版本不兼容时的显式降级 + 账本累计正确）；
- 体验①：SSE 帧单测 + 断线重连 + 半截失败（`report_reset`）+ 本地手动冒烟（前端渐进渲染）；
  体验②：标定脚本 + 对照实验方案 + 决策记录；
- 全量套件 0 失败（干净 PG 容器）。

## 10. 排期

| 步骤 | 内容 | 依赖 | 粗估 |
|---|---|---|---|
| 0 | TBD-0~7 拍板 | — | ✅ 已完成（2026-10-12） |
| R1 | 来源身份分组（12.4，含回归夹具） | 拍板 | 1~2 天 |
| R2 | evidence manifest（E7，含有序清单/两验收层） | R1 | 2~3 天 |
| R3 | 跨进程恢复（E6，含 lock/账本对账/双路径演练） | R2 | 3~5 天 |
| R4 | 报告流式（体验①，含重连/失败协议） | 可并行 | 1~2 天 |
| R5 | 档位耗时标定 + 实验方案（体验②） | 可并行 | 1 天 + 决策 |
| 收口 | 断言/演练/文档 | 全部 | 1 天 |

合计 ≈ **2~3 周**（R1→R2→R3 为关键路径；R4/R5 可并行插入）。

## 11. 变更记录

| 日期 | 类型 | 原因 | 改动摘要 | 关联 PR/commit |
|---|---|---|---|---|
| 2026-10-12 | 文档 | 批次 2 立项（roadmap §3） | 建稿：12.4/E7/E6 现状盘点（含代码行号）+ 体验①②；TBD-0~7（推荐 + 待确认）；DoD/排期；Issue #201 | 本文档 |
| 2026-10-12 | 评审 | 设计评审（主理人）6 点闭合 | ① 身份去 `content_hash`、`generation` 进契约与分组（`chunk_id` 格式验收锁定；metadata 显式存）；② 预算以 `usage_ledger` 为累计事实源 + 对账规则 + 「节点已调用未完成即 kill」场景；③ manifest 增 **Writer 有序工作清单** + 两验收层（完整性可校验 / 保留期内原文可取得）+ RAG 保留策略；④ 执行模式 `resumed`/`replayed` 显式落库、重跑不冒充恢复（演练双路径断言）；⑤ 流式 delta 持久化粒度 + `report_reset` 协议 + 断线重连/半截失败测试；⑥ R5 限定为「是否值得并行实验」+ 固定题集对照；TBD-0 补确定性 RAG 回归夹具（「未覆盖 ≠ 无影响」） | 本文档 |
| 2026-10-12 | 拍板 | TBD-0~7 逐项拍板（主理人） | **全部按 v2 推荐**：①不采全量 before 基线 + RAG 回归夹具；②rag 身份 `sha256(scope|doc_id|chunk_id)`（不含哈希）+ metadata 存 generation；③分组键 `(doc_id, generation, sq_id)`；④manifest 落 `run_artifacts`（有序工作清单 + 两验收层 + RAG 保留窗口）；⑤账本为事实源 + 只做从节点恢复 + resumed/replayed 落库；⑥官方 checkpoint-postgres；⑦流式并入本批（delta 粒度 + `report_reset`）；⑧R5=是否值得并行实验 | 本文档 |
| 2026-10-12 | 实现 | R1 来源身份分组交付 | `evidence.py` 增 `compute_finding_evidence_id`（rag=版本化位置 `sha256(scope|doc_id|chunk_id)`，**不含 content_hash**；web/arxiv/legacy 保持内容寻址）+ `evidence_group_key`（rag=`(doc_id, generation, sq_id)`）+ `rag_chunk_generation`（格式锁）；`researcher.py` RAG metadata 增 `generation`；`context/manager.py` 压缩分组改身份键（同名不同文档不混编）；回归夹具 10 条 + 全量套件 0 失败 | 本文档 |
| 2026-10-12 | 实现 | R2 evidence manifest 交付 | 新增 `research_engine/evidence_manifest.py`（有序工作清单 + 引用 span + 修复记录 + 规格版本；retrieval 模式解析含 **via_origins**——压缩证据沿 origin 链；`manifest_version=1`）；`persistence.persist_terminal` 增 `evidence_manifest` 参数（终局**同事务**写产物 + 并入导出 result 副本；run_events payload 不重复携带）；runner/worker 终局路径构建并传入；单测 7 条 + 全量套件 0 失败 | 本文档 |
| 2026-10-13 | 实现 | R3 跨进程恢复交付 | 依赖：`langgraph-checkpoint-postgres`（`>=3.1,<4`；lock 外科式 `==3.1.2`）；`web/backend/checkpoint.py`（进程级单例 + 幂等 `setup()` + fail-silent + atexit 关池）；`state.py` 增 `CHECKPOINT_SCHEMA_VERSION=1`；`graph.py` checkpointer 可注入 + `iter_run(resume=)`（`stream(None)` + 跳过恢复首帧）；`worker.py` `resolve_execution_mode`（fresh/resumed/replayed + 版本比对 + 账本注入）+ 终局 `max(state, 账本)` 对账 + `RUN_STARTED`/终局 payload 留痕；单测 8 + PG 契约 2 + **演练 2**（kill→接管→续跑双路径）；全量套件 0 失败；运维语义 `release-runbook.md` §7.2 | 本文档 |
