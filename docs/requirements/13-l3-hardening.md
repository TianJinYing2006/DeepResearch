# 需求 13：L3 上线前硬化（架构审计复核后的后续需求）

> 状态：**草稿**；实施进度以 `docs/project-status.md` 为唯一正源（D-01）。
> 来源：外部「架构与需求落地审计报告」（2026-09-29）+ 本仓库逐条复核（记录见本文 §4）。
> 复核基线：dev `8762ac9`（迁移 `0001`~`0017`）。
> 审计报告自称基线 `e506ec5` / 迁移仅 `0001`~`0014` —— **该 commit 在本仓库不存在**（`git log --all` 无）；
> 其报告的 4 个 P0 与 5 项一致性问题共 9 项阻断已在 **PR #54（2026-09-28）** 落地（证据见 §4.1）。
> 本文只把**仍未落地**的条目固化为可验收的后续需求，已落地项登记为历史记录，避免重复立项。

## 1. 元信息

| 项 | 值 |
|---|---|
| 编号 | 13 |
| 标题 | L3 上线前硬化（架构审计复核后的后续需求） |
| 优先级 | P0（H1）/ P1（H2）/ P1~P2（H3） |
| 状态 | 草稿 |
| 负责人 | TianJinYing2006 |
| 关联 Issue | 待建 |
| 关联 PR | |
| 创建 / 更新 | 2026-09-29 |

## 2. 问题背景

### 2.1 现状一句话

> 代码底座已具备「邀请制内测」能力（PG 事实源 + Worker 队列 + 终局事务 + SSE 回放 + 账号配额 + 审核申诉 + 观测告警），
> **但尚未完成真实云 staging 验证与生产准入**；审计提出的阻断项多数已修，剩余的是「上 staging 前必须闭合」的一致性/边界问题。

### 2.2 审计与复核的关系（基线勘误）

| # | 审计报告的说法 | 复核结论（本仓库实测） |
|---|---|---|
| 1 | 基线 `e506ec5`，迁移 `0001`~`0014` | 无此 commit；实际 dev `8762ac9`，迁移已到 `0017`；报告描述的是 PR #54 之前的状态 |
| 2 | 「测试数字不一致：566 vs 730」 | 两者是不同 commit 的历史快照（09-26 P8-A 时 566、09-27 时 730），非矛盾；当前本地实测收集 **748 条**，与 `docs/project-status.md` 2026-09-28 记录一致 |
| 3 | 「P2 已写成全部完成」 | `project-status.md` 按「代码+CI 已交付」记录；`docs/operations/release-runbook.md` 明确「应用回滚演练待 staging，未演练不得视为已验证」；`production-readiness.md` 仍为草稿、G1~G8 未开始 |

### 2.3 为什么现在要做

审计剩余条目（对象存储一致性、RAG 文档列表、tenant 贯通、队列 fencing、staging 生产化）如果不先闭合，
会出现「本地/单节点跑得通、多实例下出现孤儿对象/重复計费/旧 Worker 越权写终局」这类**只在真实环境暴露**的问题，
后续每个功能批次都要返工。故把审计记录固化为一次性硬化的需求基线，按 H1~H3 阶段验收。

## 3. 需求分析

### 3.1 目标

- 把审计的开放条目变成**有代码位置、有 DoD、有测试策略**的需求条目；
- 每项都对齐现有工程原则：PG 为事实源、外部副作用可修复、fail-closed 优先、留痕不可静默。

### 3.2 阶段划分

| 阶段 | 含义 | 前置关系 |
|---|---|---|
| H1 | staging 部署前必须闭合的一致性/边界项 | 无 |
| H2 | 真实云 staging 部署与生产化拓扑 | H1 完成后 |
| H3 | 容量标定、发布回滚、财务对账、队列公平性 | H2 有真实环境后 |

### 3.3 可量化成功定义

- H1 四项全部有真实 PG/Redis 集成测试 + 迁移 checks；
- H2 完成一次真实云 staging 部署，健康探针、SSE、审核链路在 staging 全通；
- H3 压测指标表回填（`docs/operations/capacity-model.md`）+ 发布回滚演练记录归档。

## 4. 当前设计与复核记录

### 4.1 已落地（PR #54，2026-09-28；登记为历史记录）

| # | 审计条目 | 落地证据（代码/迁移/测试） |
|---|---|---|
| 1 | `cleared` 与 CHECK 约束冲突（P0-1） | `migrations/0015_moderation_lifecycle.sql`：取值域扩为 `flagged/blocked/cleared/under_review`；`migrations/checks/0015_schema_assert.sql` 真实 PG 断言；`web/backend/store.py:1387-1428` accepted 与 `run=cleared` 同事务；`tests/test_appeals.py` |
| 2 | 月度预算非预留式（P0-2） | `migrations/0016_quota_reservations.sql`（hold/settle）；`store.py:243` 准入同事务预留、`store.py:900` 终局结算；`tests/test_run_store.py`/`test_quotas.py` |
| 3 | provider 降级 fail-open（P0-3） | `web/backend/moderation_providers.py` 三策略 `quarantine`(默认)/`fail_closed`/`allow`；`moderation.py:159-163` degraded 映射 `under_review`/`blocked`；`tests/test_moderation_providers.py` |
| 4 | 审核两次扫描 / 证据跨事务（P0-4） | `moderation.py` `evaluate_output` 单次扫描产出不可变 `ModerationDecision`；`persistence.py:81` `kind=output_decision` 随终局同事务；`redact_public_payload` 递归覆盖 `report/raw_report/report_md/report_text/full_text/markdown/body/summary`；`tests/test_output_gate.py` |
| 5 | 申诉 claim 不校验返回值 / 归属越权 | `web/backend/appeals.py:55-70` claim 失败即返回并校验 reviewer 归属；`store.py:1396` 决策单事务 |
| 6 | 申诉建单与接受非原子 | `store.py` `create_appeal` 同事务写 `moderation_records` + 审计；`decide_appeal` 单事务完成状态更新与留痕 |
| 7 | `/api/metrics`、`/api/ops/*` 无应用层鉴权 | `main.py:674` `_require_ops`（`DR_OPS_TOKEN`；staging/生产未配置即 401）；`main.py:1399/1426` 调用；`tests/test_ops_api.py` |
| 8 | 可信代理配置粗糙 | `DR_TRUSTED_PROXY_CIDRS` + `DR_PROXY_HOPS`（`main.py:266-267,776-789`） |
| 9 | usage ledger 静默吞异常 | `research_engine/usage.py:61-70` 失败计数 + ERROR；`web/backend/usage.py:45-52` 写审计 + Prometheus；`DR_USAGE_STRICT` |
| 10 | RAG 上传去重竞态 | `migrations/0017_rag_active_unique.sql` 活跃 `doc_id` 唯一索引；`store.py` `INSERT ... ON CONFLICT DO NOTHING` |

### 4.2 仍未落地（本文的需求范围）

| # | 条目 | 现状证据 |
|---|---|---|
| 1 | tenant 未端到端贯通 | `research_engine/rag/scope.py:31-33` 支持 `tenant_id`，但 `worker.py:411`、`runner.py:607` 只传 `user_id`；无 `tenants/tenant_memberships` 表 |
| 2 | 文档列表仍依赖 Qdrant 全量扫描 | `main.py:1724` `scroll_all` 后置过滤；`research_engine/rag/store.py:174-190` 无 offset 分页；PG `rag_ingestions` 已有数据但未作为列表事实源 |
| 3 | 对象存储无一致性状态与修复机制 | `persistence.py:106-121` 先写 S3 再同事务落元数据（失败留 orphan）；`0012` 无 `artifact_status/verified_at/delete_after`；无 orphan/missing 扫描器 |
| 4 | 队列缺 fencing token 与公平性 | `store.py:590` `claim_run` 只有 `claimed_by + lease`；无 `lease_version` 防旧 Worker 越权写终局；无 per-tenant 调度/aging |
| 5 | staging 非生产拓扑 | `docker-compose.staging.yml:95` `minio/minio:latest` 未锁版本；默认口令 `deepresearch-local`；无 TLS/反代/资源上限/cap_drop；Qdrant 指向宿主机外部（`:16`） |
| 6 | 上传隔离区依赖共享卷 | `docker-compose.staging.yml:152-153,190-191` API/Worker 共享 `rag_quarantine` 卷（单机可用，多副本不成立） |
| 7 | 跨域 SSE 未验证 | `web/frontend/src/hooks/useResearchStream.ts:83` `EventSource` 无 `withCredentials`；CORS 无 `allow_credentials`（仅同源/宽松开发场景成立） |
| 8 | 告警真实触达与中心化监控未验证 | 判定/webhook 代码就位（`migrations/0013_alert_deliveries.sql` + `web/backend/worker.py` 告警判定 + `store.py` 投递/退避）；真实 webhook 地址与 Prometheus/OTel 收集端未接 |
| 9 | 发布回滚演练未执行 | `docs/operations/release-runbook.md` 已交付；备份恢复演练 CI 常跑，应用回滚演练待 staging |
| 10 | 财务口径未对账 | `0011` usage ledger 已有 `cost_source`，但无 provider 账单对账与 `reconciliation_status` |

### 4.3 设计建议（不阻塞，见 §5.4）

- `runs.moderation_status` 语义仍偏重（审核结果/流程/申诉/导出策略四合一）；长期建议拆 `moderation_cases/decisions/appeals` + `runs` 只留派生 `content_access_state`；
- 文档状态建议拆四态：代码 / 自动化测试 / staging 验证 / 生产准入（当前 `production-readiness.md` 与 `project-status.md` 口径需互链说明）。

## 5. 优化方案（后续需求条目）

### 5.1 H1：staging 前必须闭合（P0）

**H1-1 对象存储一致性**
- 现状：先 S3 后 PG；`0012` 无产物状态字段；无孤儿扫描。
- 目标：`run_artifacts` 增 `artifact_status(pending/available/deleting/deleted/missing)`、`verified_at`、`delete_after`、`last_error`；上传 = 先建 pending → 写对象 → 校验 hash/size → available；删除 = PG 标记 deleting → outbox 删对象 → 验证不存在 → deleted；后台 orphan/missing 扫描 + `artifact-repair` CLI。
- DoD：真实 PG + MinIO 契约测试（写失败留 pending、删失败可重试、扫描器可发现手工删除的对象）；`000x` 迁移 checks；导出/读取路径兼容 `pending/missing` 的显式错误。

**H1-2 RAG 文档列表 PG 化 + Qdrant scroll 分页**
- 现状：`main.py:1724` 全量 `scroll_all` + Python 聚合；`rag/store.py:174-190` 无 offset。
- 目标：列表/计数以 PG `rag_ingestions` 为事实源（含分页）；Qdrant 仅作派生索引；`scroll_all` 服务端 filter + `offset` 分页 + 固定 page size（保留后置过滤兜底）。
- DoD：`GET /api/rag/docs` 返回值在「Qdrant 与 PG 不一致」时以 PG 为准并有告警；分页参数测试；万级 points 的滚动分页单测（可用 fake client）。

**H1-3 队列 fencing token**
- 现状：`claim_run` 仅 `claimed_by + lease_expires_at`（`store.py:590`）；极端情况下过期租约的旧 Worker 仍可写终局。
- 目标：`runs.lease_version` 每次认领递增；Worker 的所有状态写入携带 `lease_version` 条件（`WHERE run_id=? AND lease_version=?`）；终局事务校验不通过即放弃并记审计。
- DoD：真实 PG 并发测试（旧 token 写终局被拒）；`finalize_run`/`persist_terminal` 路径全覆盖；清扫重排时递增。

**H1-4 tenant 最小贯通**
- 现状：`tenant_id` 字段存在但未贯穿（§4.2-1）。
- 目标：新增 `tenants` / `tenant_memberships`；run 创建时固化 `tenant_id + membership_role + policy_snapshot`；ingestion/Qdrant filter/quota/audit/deletion 全链路带 `tenant_id`；保留 `user_id` 作为基础隔离边界。
- DoD：真实 PG 隔离测试（同 tenant/跨 tenant 可见性矩阵）；Qdrant 服务端 filter 测试；不引入企业 ACL 复杂度（域模型最小化）。

### 5.2 H2：真实云 staging（P1）

**H2-1 生产化拓扑与硬化**
- 云上 staging 实际部署（多 API + 多 Worker）；secret manager 替代默认口令；镜像 pin（版本+digest）+ SBOM + 漏洞扫描；TLS + 反向代理 + WAF + 请求体/连接限制；容器 `read_only/cap_drop/no-new-privileges/pids_limit/资源上限`；Qdrant 托管或独立实例；`DR_ENV=staging` 启动自检全过。
- DoD：一次完整 staging 冒烟（注册→研究→SSE→报告→导出→审核→申诉）；staging 配置与 `production-readiness.md` 清单逐项登记。

**H2-2 告警真实触达 + 中心化监控**
- 真实 webhook（飞书/钉钉/企微）触达验证；Prometheus 抓取 + OTel 收集端接入；关键告警（队列深度、stale lease、审核逾期、删除失败、5xx、预算）在真实环境演练一次 firing/resolved。
- DoD：告警演练记录（时间、渠道、指纹去重、恢复通知）落 `docs/operations/`。

**H2-3 上传隔离区对象存储化**
- API 流式上传 → 对象存储 quarantine bucket → PG 记录 `object_key/hash/size/status` → Worker 短期凭证读取 → 解析/embedding → 清除/转正；本地卷保留为 local/单节点开发路径。
- DoD：多副本 compose 下上传/摄取全通；本地卷路径仍可用（双轨）。

### 5.3 H3：容量与恢复（P1~P2）

| # | 条目 | DoD |
|---|---|---|
| H3-1 | 压测基线回填 | `capability-model.md` 实测表补齐：POST P50/P95/P99、SSE 并发、API RPS、Worker 吞吐、排队时长、PG/Redis/Qdrant 使用率、5xx 比例、成功率、超时率、单任务成本 |
| H3-2 | 发布回滚演练 | 执行 `release-runbook.md` 真实演练：旧版本 → 新迁移 → 新应用 → 产生数据 → 回滚旧镜像 → 继续消费；记录兼容性结论 |
| H3-3 | usage 对账 | provider 账单 → usage ledger 差异报告 → 修正记录路径；`0011` 增 `reconciliation_status`（如需） |
| H3-4 | 队列公平性 | per-tenant/per-user 并发与 aging；PG 内 round-robin 最小实现（不引中间件）；队头阻塞测试 |

### 5.4 设计建议（不阻塞，随阶段顺带或另立）

- **moderation 域拆表**：`moderation_cases/decisions/appeals` + `runs.content_access_state` 派生字段；迁移期双写可后置。
- **跨域 SSE**：若最终非同源部署，需 `EventSource withCredentials` + CORS `allow_credentials` + `SameSite=None; Secure` + CSRF 覆盖；同源部署则维持现状并写入部署文档。
- **文档四态表**：`production-readiness.md` 每项按「代码 / 自动化测试 / staging 验证 / 生产准入」四态登记，与 `project-status.md` 互链。

## 6. 设计策略

- **不推倒重写**：保留 PG 事实源、`SKIP LOCKED` 认领、租约/清扫、终局事务、SSE 回放、Qdrant 派生索引、Argon2id/CSRF、usage ledger、append-only audit、outbox 删除等已定型决策（审计报告亦认可）。
- **只做事务边界与领域边界演进**：先修一致性（H1），再真实环境验证（H2），最后容量与恢复（H3）；引入中间件（Kafka/K8s 等）需 H3 实测数据支撑后再议。
- **双轨兼容**：所有 H1/H2 迁移均保留旧路径回落（如 db/s3 双轨、本地卷/对象存储双轨），避免一次性切换风险。

## 7. 验收标准（DoD）

- [ ] H1-1 对象存储一致性：状态字段 + 修复流程 + 真实 PG/MinIO 测试
- [ ] H1-2 RAG 列表 PG 化 + scroll 分页与 server-side filter
- [ ] H1-3 队列 fencing token + 旧 Worker 越权写终局被拒的 PG 测试
- [ ] H1-4 tenant 最小贯通（tenants/memberships + 全链路 tenant_id）
- [ ] H2-1 云 staging 部署 + 拓扑硬化清单逐项登记
- [ ] H2-2 告警真实触达 + 监控收集端 + 一次 firing/resolved 演练记录
- [ ] H2-3 隔离区对象存储化（保留本地卷开发路径）
- [ ] H3-1 压测实测表回填
- [ ] H3-2 应用回滚演练完成并归档
- [ ] H3-3 usage 对账与差异报告
- [ ] H3-4 队列公平性最小实现与队头阻塞测试
- [ ] 全程零重复立项：§4.1 已落地项不再出现在任何批次的实施清单中

## 8. 影响范围与风险

- **影响模块**：`web/backend/store.py`/`persistence.py`/`worker.py`/`runner.py`/`main.py`、`research_engine/rag/*`、`migrations/`、`docker-compose.staging.yml`、`docs/operations/*`。
- **风险**：
  1. 迁移与旧数据兼容（`artifact_status`、`lease_version` 需回填默认值；旧 run 不阻塞）；
  2. fencing token 与既有运动路径（force-stop、清扫接管）交错，需专项交错测试；
  3. tenant 贯通涉及面广，若不控制范围会演变成企业 ACL 工程 —— 本条只做最小隔离矩阵；
  4. staging 依赖外部资源（云、域名、证书、Qdrant 托管）—— 属外部前置，需提前排期。
- **降级/兜底**：所有新路径保留显式降级分支（S3 失败落 db、扫描器只标记不自动删、回滚 runbook 可退回旧镜像）。

## 9. 测试策略

- **单测**：状态机/分页/对账纯函数；fencing token 条件写；artifact 状态迁移。
- **集成（CI `infra` job，真实 PG/Redis）**：迁移 checks 新增断言；租约交错与越权写入拒绝；tenant 隔离矩阵；MinIO 契约（可条件跳过）。
- **staging 演练**：H2 冒烟脚本 + 告警 firing/resolved +（H3）回滚演练。
- **回归面**：现有 748 收集（704 通过 + 44 跳过）全绿为准入基线；每项 H1 迁移必须带 `migrations/checks/*.sql` 断言。

## 10. 变更记录

| 日期 | 类型 | 原因 | 改动摘要 | 关联 PR/commit |
|---|---|---|---|---|
| 2026-09-29 | 建稿 | 外部架构审计复核收口 | 审计记录归档 + 基线勘误 + 未落地 10 项固化为 H1~H3 需求 | |
