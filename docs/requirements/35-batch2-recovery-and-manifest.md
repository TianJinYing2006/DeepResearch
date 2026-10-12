# 需求-35-batch2-recovery-and-manifest

> 状态流转：草稿 → 进行中 → 自测 → 待合 → 已合
> **来源**：`docs/roadmap.md` §3（批次 2：恢复与清单 + 体验）；挂账映射 **12.4**（R09）/ **12.2**（E7）/ **12.10**（E6）+ 体验①②。
> **前置决策（P0）**：12.4 会改 RAG 证据身份/去重口径（评测路径无 KB ⇒ 影响面≈0）；E7/E6/体验不改输出面。
> 按 W8 纪律立项时先拍板 before 基线 —— **建议不采**（记录见 §5 TBD-0，待主理人拍板）。
> 飞书镜像：https://wcnnpvbxd7li.feishu.cn/docx/C5afdrWMGoqhlux1FFUcsCyDntb（`C5afdrWMGoqhlux1FFUcsCyDntb`，
> 位于 `DeepResearch 需求文档` 文件夹，lark-cli 直建）。

## 1. 元信息
| 项 | 值 |
|---|---|
| 编号 | 35 |
| 标题 | 批次 2「恢复与清单」：来源身份分组（12.4）+ evidence manifest（E7）+ 跨进程恢复（E6）+ 体验①② |
| 优先级 | P1 |
| 状态 | 草稿 |
| 负责人 | TianJinYing2006 |
| 关联 Issue | #201 |
| 关联 PR | — |
| 创建 / 更新 | 2026-10-12 |
| 前置评审 | TBD-0~7 待主理人逐项拍板（见 §5） |

## 2. 问题背景

- **12.4（R09，复核已复现）**：两个**不同 `doc_id`** 的 `report.pdf` 相同正文片段得到同一
  `evidence_id`（`evidence.py:45-47`：`sha256(source + "\\n" + content)[:16]`，而 RAG 的
  `source = "rag:<filename>"`（`researcher.py:199-213`）不含文档身份）⇒ `dedupe_new_findings`
  按 `(evidence_id, sq_id)` 去重（`evidence.py:76-110`）后**后到的同片段被误丢**；
  压缩按 `(source, sq_id)` 分组（`context/manager.py:46-48`）⇒ **同名不同文档混编**摘要，
  且只保留首个文档定位。
- **E7（12.2 R07）**：终局 payload 只存 `evidence_index`（原文层轻量索引：id/hash/locator/时间，
  **不含正文**，`worker.py:593` / `runner.py:748`）+ citations；**不存** `working_findings` 与
  「编号 N → 工作证据 → origin IDs」映射 ⇒ 压缩改变列表长度与顺序后，数字引用**不能按位置反推**；
  hash 只能验证拿到的内容，不能恢复没保存的内容。
- **E6（12.10）**：图仍用 `MemorySaver`（`graph.py:165`，进程内）；进程重启后 Worker 租约接管是
  **重跑**，不是节点继续执行。前置：外部调用幂等（LLM 不可幂等）、预算累计口径、manifest 版本（E7）。
- **体验①**：Writer 为单次 `smart_chat`（`writer.py:117`），**无 token 流**；SSE 只有节点级
  `STEP_FINISHED` 帧 ⇒ 长写作期间前端无正文增量。
- **体验②**：quick/standard/deep 三档（`web/backend/profiles.py`）**耗时未标定**（无 p50/p90）
  ⇒ 「是否做子问题级并行（Send）」缺数据。

## 3. 需求分析

目标（一句话）：**证据身份与内容解耦、证据链可持久化复现、异常可续跑**（体验项为体感增量）。

- **R1（12.4）**：证据身份 = **出现位置**（用户作用域 + `doc_id`/版本 + `chunk_id`/span），
  blob 内容 hash 与之分离（blob 可复用）；同一片段可有多个 provenance；**展示名不承担身份职责**。
- **R2（E7）**：**版本化 evidence manifest**：工作编号 → 稳定 ID → origin IDs → 快照引用
  （`snapshot_id`）→ 文档版本/locator → claim span → 修复前后记录 → 规格版本；遵循用户隔离与
  保留期；**不必**全部内嵌到 SSE 或用户下载文件。
- **R3（E6）**：持久化 checkpoint（含版本）⇒ **从节点恢复**；先明确外部调用幂等、预算累计与
  「SSE 回放 / 重新执行 / 从节点恢复」三语义边界（回放与重跑已有，本批新增恢复）。
- **R4（体验①）**：Writer 报告流式产出（SSE 渐进正文；失败降级回非流式）。
- **R5（体验②）**：档位耗时标定（各档 p50/p90）⇒ 决策「Send 并行做不做」。

## 4. 当前设计（代码现状）

| 层 | 位置 | 现状 | 本批次处置 |
|---|---|---|---|
| 证据 ID | `evidence.py:45-47` | `sha256(source + 正文)` 前 16 位 | **R1**：身份要素按 source_type 分派 |
| RAG 身份 | `researcher.py:199-213` | `source="rag:<filename>"` + metadata `doc_id/chunk_id/locator` | **R1**：`doc_id` 进身份 |
| 去重 | `evidence.py:76-110` | `(evidence_id, sq_id)` | **R1**：id 本身含位置（去重键不变） |
| 压缩分组 | `context/manager.py:46-48` | `(f.source, f.sq_id)` | **R1**：按身份键分组（rag 用 doc 维度） |
| 终局 payload | `worker.py:593` / `runner.py:748` | `evidence_index`（轻量，无正文） | **R2**：manifest 落盘 |
| checkpoint | `graph.py:165` | `MemorySaver`（进程内） | **R3**：PG checkpointer |
| 恢复语义 | Worker 租约接管 | **重跑** | **R3**：checkpoint 存在 ⇒ 从节点续跑 |
| Writer | `writer.py:117` | 单次 `smart_chat` | **R4**：`chat_stream` + SSE `report_delta` |
| 档位耗时 | `profiles.py`（三档） | 未标定 | **R5**：p50/p90 标定 + 决策 |

## 5. 优化方案（推荐 + 待确认）

- **TBD-0（前置，P0）before 基线**：**建议不采**——12.4 仅影响 RAG 路径（评测路径无 KB，
  影响面≈0）；E7 是**新增产物**不改输出面；E6 只影响异常路径；体验①② 为展示/标定。
  配对结论仍随 **12.11 新基线**（批次 2 后段）统一重建。**待确认。**
- **TBD-1（R1 身份设计）**：推荐按 source_type 分派 `evidence_id` ——
  - `rag`：`ev_` + `sha256(scope | doc_id | chunk_id | content_hash)[:16]`（`content_hash`
    保留复核职责；同片段多位置 ⇒ **多个 id**，天然不误去重）；
  - `web` / `arxiv`：**保持** `source + content`（URL / abs 即位置）；
  - 「同一片段多 provenance」本批**只留接口**（`metadata["provenances"]` 预登记，不实现），
    多 id 已表达位置语义。**待确认。**
- **TBD-2（R1 压缩分组）**：推荐分组键改**身份键**——`rag` 用 `(doc_id, sq_id)`，
  其余 `(source, sq_id)`（统一经一个 `evidence_group_key(f)` 帮助函数）。**待确认。**
- **TBD-3（R2 manifest 形态）**：推荐 `run_artifacts` 新 kind **`evidence_manifest`**（JSON；
  `manifest_version=1`；终局同事务写入），**不上新表**（避免再开备份/保留面）；快照引用沿用
  `0022`（用户隔离与保留期已具备）。**待确认。**
- **TBD-4（R3 语义与幂等）**：推荐本批**只实现「从节点恢复」**；幂等边界 = **节点边界**
  （恢复后重执行未完成节点，LLM 调用**可能重复 ⇒ 如实累计记账，不做去重**）；
  预算 = 从 checkpoint state 恢复 `token_used`（**不重置**）；三语义写入运维文档。**待确认。**
- **TBD-5（R3 存储选型）**：推荐官方 **`langgraph-checkpoint-postgres`**（`thread_id=run_id`；
  与现有接管共存：checkpoint 存在 ⇒ 续跑，否则重跑）；依赖入 lock（**lock 重生成**）；
  checkpoint 带版本字段与兼容策略。**待确认。**
- **TBD-6（R4 范围）**：推荐 `LLMClient.chat_stream`（DashScope OpenAI 兼容流式）+ Writer 增量发
  SSE 新帧 `report_delta` + 前端渲染；**范围确认**：并入本批（同需求跟踪）or 单独小需求。**待确认。**
- **TBD-7（R5 标定口径）**：推荐用真实 run 数据（`runs.started_at/finished_at` +
  `request.profile`）统计各档 p50/p90；样本不足（内测量）时允许 quick 档补测；产出决策记录
  （Send 做/不做）。**待确认。**

## 6. 设计策略

- **身份与内容解耦**：blob 可复用、身份不可复用（12.4 核心；ADR-0011 精神延续）；
- **只增不改**：manifest 版本化；历史产物不回填（D-14 纪律：写侧只出新键 + 读侧 dual-read）；
- **异常路径专属**：E6 只影响「接管/重启」，正常路径零行为变化；
- **不新增外部服务**：checkpoint/manifest 全部复用 PG（与 N1 备份面一致）；
- **体验项独立小 PR**：R4/R5 不阻塞主线（R1→R2→R3）。

## 7. 验收标准（DoD）

- [ ] TBD-0 决策：**建议不采**（未采须在文档记录理由）
- [ ] R1：证据身份含位置（rag：scope+doc_id+chunk_id；web/arxiv 保持）；去重/压缩按身份键；
      **回归锁**：同名不同文档同片段不再误去重（新增单测）
- [ ] R2：`evidence_manifest` 终局落盘（run_artifacts；版本字段；含编号→工作→origin→快照→
      claim span→修复前后）+ 导出可读
- [ ] R3：PG checkpoint 落盘 + Worker 接管续跑（checkpoint 存在时）；幂等/预算口径文档化；
      单测：恢复后 `token_used` 不重置、异常路径不回归
- [ ] R4：Writer 流式（`chat_stream` + SSE `report_delta` + 前端）；失败降级回非流式
- [ ] R5：档位耗时 p50/p90 报告 + Send 决策记录
- [ ] 测试：单测（身份/去重/分组/manifest 结构/checkpoint 往返）+ 真 PG 契约（manifest 写入、
      checkpoint 表）+ 全量套件绿 + 恢复演练（kill → 接管 → 续跑，本地 compose）
- [ ] 文档：需求 27 §12.2/§12.4/§12.10 状态回填；口径断点登记（若动输出面）；
      README/运维文档同步（落地才写）

## 8. 影响范围与风险

- 动：`evidence.py`、`context/manager.py`、`agents/researcher.py`（R1）；`worker.py`/`runner.py`/
  导出（R2）；`graph.py` + worker 接管路径 + `requirements-lock`（R3）；`llm/client.py`/
  `writer.py`/`streaming.py`/前端（R4）。
- 风险①：身份变更影响历史去重行为 ⇒ **不回填历史**（新键只对新产物生效）；
- 风险②：checkpoint 依赖 LangGraph 版本 ⇒ 锁版本 + 兼容字段；升级演练登记；
- 风险③：恢复路径**重复计费**（LLM 不可幂等）⇒ 如实记账 + 文档明示（不静默）；
- 风险④：流式涉及前端 ⇒ `check_frontend_boundary.py` 守卫不越界（N4 精神）。
- 兜底：R1~R3 全部 fail-open 到现状（身份退化为现状计算；checkpoint 不可用 ⇒ 重跑；流式失败 ⇒ 非流式）。

## 9. 测试策略

- 单测（离线）：身份分派（rag/web/arxiv）、同名不同文档不误去重、分组键、manifest 结构
  （版本/字段齐备）、checkpoint 往返（stub）；
- 真 PG 契约（CI `infra` lane）：manifest 写入/读取、checkpoint 表读写与版本；
- 恢复演练：本地 compose 中 kill worker → 接管 → 断言「从节点续跑、预算不重置、产物一致」；
- 体验①：SSE 帧单测 + 本地手动冒烟（前端渐进渲染）；体验②：标定脚本 + 决策记录；
- 全量套件 0 失败（干净 PG 容器）。

## 10. 排期

| 步骤 | 内容 | 依赖 | 粗估 |
|---|---|---|---|
| 0 | TBD-0~7 拍板 | — | 0.5 天 |
| R1 | 来源身份分组（12.4） | 拍板 | 1~2 天 |
| R2 | evidence manifest（E7） | R1 | 2~3 天 |
| R3 | 跨进程恢复（E6，含 lock/演练） | R2 | 3~5 天 |
| R4 | 报告流式（体验①） | 可并行 | 1~2 天 |
| R5 | 档位耗时标定（体验②） | 可并行 | 1 天 + 决策 |
| 收口 | 断言/演练/文档 | 全部 | 1 天 |

合计 ≈ **2~3 周**（R1→R2→R3 为关键路径；R4/R5 可并行插入）。

## 11. 变更记录

| 日期 | 类型 | 原因 | 改动摘要 | 关联 PR/commit |
|---|---|---|---|---|
| 2026-10-12 | 文档 | 批次 2 立项（roadmap §3） | 建稿：12.4/E7/E6 现状盘点（含代码行号）+ 体验①②；TBD-0~7（推荐 + 待确认）；DoD/排期；Issue #201 | 本文档 |
