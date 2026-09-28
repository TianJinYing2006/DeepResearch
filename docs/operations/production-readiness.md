# 生产上线准入清单（L3）

> **定位**：从「开发完成」到「可以对外」之间的**运营与上线视角准入清单**。
> 本文档管「上线前必须成立的事实」，随实施更新勾选；需求与参数口径见
> `docs/requirements/10-l3-production.md`，架构决策见
> `docs/decisions/0009-l3-multiuser-production.md`，项目总看板见 `docs/project-status.md`。
>
> **三条判定原则**：
> 1. 「有备份」以**恢复演练通过**为准 —— 没有恢复演练，不认为有备份；
> 2. 「有监控」以**告警可触达**为准 —— 只看板不告警等于没有监控；
> 3. 「合规已确认」以**书面/可追溯的属地答复与落档记录**为准 —— 不以口头或搜索结论代替。
>
> 状态：**草稿**（P0 立项，2026-09-24）。拍板前所有条目默认未开始。

## 1. 上线准入 Gate

| # | Gate | 准入标准（必须成立的事实） | 当前状态 |
|---|------|---------------------------|---------|
| G1 | 任务不丢 | API 重启 / 发布 / worker 崩溃后，任务状态与报告不丢；失败进入终局或可重试 | 未开始 |
| G2 | 用户隔离 | 查询 / 取消 / 导出三路均无法越权访问他人任务；RAG 检索带强制租户 filter | 未开始 |
| G3 | 成本可控 | 单用户日/月预算与全局熔断生效；超限自动停止并留痕 | 未开始 |
| G4 | 内容安全 | 输入预检 + 输出审核 + 审核记录 + 申诉/复核/封禁链路可演练 | 未开始 |
| G5 | 可恢复 | 恢复演练通过 1 次：删库 → 从备份恢复 → 报告与任务可用 → 越权核验 | 未开始 |
| G6 | 可观测 | 关键指标可查、阈值告警可触达值班人（不低于邮件/IM） | 未开始 |
| G7 | 合规确认 | ICP 备案完成或明确计划；生成式 AI 相关手续经属地确认并落档；数据流向定案 | 未开始 |
| G8 | 可回滚 | 发布有灰度与回滚路径；数据库迁移可回滚且发布前兼容 | 未开始 |

## 2. 环境矩阵（待填）

| 环境 | 用途 | 主机 / 域名 | PostgreSQL | Redis | 配置来源 | CORS | 密钥管理 | 状态 |
|------|------|------------|------------|-------|---------|------|---------|------|
| local（现状） | 开发 / 演示 | `127.0.0.1:8000` + Vite `5173` | 无（内存态） | 无 | `.env` | 固定 localhost（`main.py:47-52`） | `.env` | 已有 |
| staging | 内测前验证 | 待填 | 待填 | 待填 | 环境变量 / 密钥服务（待定） | 环境变量控制 | 待定 | 未开始 |
| production | 正式对外 | 待填（ICP 备案前置） | 待填 | 待填 | 环境变量 / 密钥服务（待定） | 环境变量控制 | 待定 | 未开始 |

## 3. 检查清单

### 3.1 部署底座

- [x] `Dockerfile.api`（多阶段构建 + 依赖锁；**API 与 Worker 共用镜像、不同入口**，不单列 `Dockerfile.worker`）
- [x] `docker-compose.staging.yml`（api / worker / PostgreSQL / Redis；反向代理待 P8）
- [ ] 数据库迁移工具与迁移脚本（**只前向 + `schema_migrations` 幂等**；回退走备份恢复，见 §7.3）
- [x] `/api/health` 拆分为 readiness / liveness，并接反向代理探针；**P1-3 起队列模式 readiness 要求 ≥1 个心跳新鲜的 worker**（`workers` 注册表，窗口 `DR_WORKER_HEARTBEAT_MAX_AGE_SECONDS=90`）
- [x] 配置分层：开发 / staging / 生产，敏感项全部走环境变量或密钥服务；**P0-9 起 `DR_ENV=staging|production` 启动自检 fail fast**（`DR_AUTH_REQUIRED` / `DR_COOKIE_SECURE` / `DR_CORS_ORIGINS` / `DASHSCOPE_API_KEY`），production 另拒明文 HTTP（`X-Forwarded-Proto`）
- [ ] **CORS 环境变量化**（禁止生产继续固定 localhost，`main.py:47-52`）
- [ ] 反向代理 SSE 专项配置：
  - [ ] `proxy_buffering off;`
  - [ ] `proxy_read_timeout` 明显大于最长运行时长（当前默认超时 3600s，`runner.py:54`）
  - [ ] `proxy_http_version 1.1;` 与 `Connection` 保持
  - [ ] 透传 `X-Accel-Buffering`（当前已在响应头设置，`main.py:229`）
- [ ] 上线前用真实浏览器验证：15s 心跳（`agui.HEARTBEAT_SECONDS`）穿过反向代理不中断
- [ ] 静态资源托管路径确认（FastAPI 托管 `frontend/dist`）
- [ ] 启动脚本、优雅停机（SIGTERM 后先停接单再收尾）

### 3.2 数据与状态

- [ ] PostgreSQL 建表：`users` / `sessions` / `runs` / `run_events` / `run_checkpoints` / `run_artifacts` / `user_quotas` / `usage_ledger` / `moderation_records` / `audit_logs`
- [ ] `run_events` 单调 `sequence` 约束（唯一索引，防重放）
- [x] 连接池与慢查询监控接入（P2-3）：`psycopg_pool.ConnectionPool` 进程内复用（懒初始化，`DR_PG_POOL_MIN/MAX/TIMEOUT_S`）；每连接 `options` 固定 `statement_timeout`（`DR_PG_STATEMENT_TIMEOUT_MS`，默认 15s）+ `application_name`；`_TimedCursor` 逐条 SQL 计时，超 `DR_PG_SLOW_QUERY_MS`（默认 500ms）记 WARNING（**只记 SQL 模板，参数不入日志**）；`RunStore.close()` 释放池
- [x] SSE 完成通知（P2-4）：事件写入（`append_event` / `finalize_run`）在**同一事务**内 `pg_notify(dr_run_events, run_id)`（提交才送达、回滚不发）；API 侧 `RunEventNotifier` 专连接 LISTEN（不走连接池、独立 daemon 线程、1s→30s 退避重连），SSE 尾随循环被唤醒即查库；**正确性不依赖通知**——`DR_SSE_POLL_SECONDS`（默认 5s）轮询兜底，断线/丢失最多增加一个轮询间隔延迟；关闭走 FastAPI shutdown，`close()` 后可重新懒启动
- [ ] Redis：队列 / 租约 / 限流键的过期策略；**不得作为唯一事实来源**（P0-2 起任务派发已完全离开 Redis，仅剩可选唤醒信号）
- [x] 对象存储：报告与导出文件的生命周期规则（与「数据保存期限」拍板值一致）。**P1-6**：MinIO（compose 服务，私有桶）+ `runs/` 前缀 **90 天**生命周期（`DR_S3_REPORT_RETENTION_DAYS`，与隐私政策「报告与运行元数据 90 天」对齐）；`run_artifacts` 只存元数据（storage/object_key/sha256/size）；未配置 `DR_S3_ENDPOINT` 回落 PostgreSQL（双轨可切换）；S3 写入失败自动回落 db；读取经应用鉴权（flagged 闸在前，不暴露直链）；readiness 含对象存储探针
- [ ] 备份策略（PostgreSQL 全量 + WAL 或等价方案；对象存储版本化）
- [ ] 恢复演练（见 §5）
- [x] RAG 检索按作用域强制过滤（P5：payload `user_id`/`tenant_id`/`visibility`；owner 只见本人 private，历史无主块向后兼容）；**P1-7 起为过滤字段建 payload index**（`user_id` 启用 `is_tenant=true`；幂等补齐，索引失败不阻断且留 `last_error`）
- [x] RAG 上传面（P6-A）：类型白名单 + `DR_RAG_MAX_FILE_MB` 上限；上传按当前用户打标，清单按作用域列出。**P0-8a 硬化**：流式落盘（不整文件入内存）、magic bytes/结构三重校验、服务端 UUID 存储名、文件名清洗、内容寻址 `doc_id`（幂等去重）、解析限额（页数/字符/分块/墙钟）、按用户上传限流；**P0-8b 异步管线**：隔离区（`DR_RAG_QUARANTINE_DIR`，compose api/worker 共享卷）→ 202 登记 → Worker 解析/embedding（解析类错误直接 rejected、供应商类错误退避重试）→ `GET /api/rag/ingestions/{id}`；`DELETE /api/rag/docs` 同步删向量并验证归零；90 天保留期自动清扫（`DR_RAG_RETENTION_DAYS`）；可选 ClamAV（`DR_CLAMSCAN_BIN` 未配置则如实 `scan_status=skipped`）

### 3.3 任务运行（Worker）

- [x] API 队列模式只创建 QUEUED 任务并投递唤醒信号（`DR_EXECUTION_MODE=queue`）；**P0-2 起派发权威在任务库**（`claim_next_queued` + `FOR UPDATE SKIP LOCKED`），Redis 信号可丢、可无
- [x] Worker 心跳（30s）与任务租约（120s）持续续期
- [x] 任务幂等键：同一 `idempotency_key` 重复提交不产生第二个 run（且不受并发闸拒绝）；**P1-1 起带请求指纹**（topic+instructions+profile 的 SHA-256）：同键不同载荷 ⇒ 409 `idempotency_conflict`，旧行（NULL）按遗留口径放行
- [x] 超时：沿用协作式节点边界检查 + 传输层硬截止口径（`runner.py:285`），Worker 侧按 `timeout_at` 在节点边界停止并落 `TIMED_OUT`
- [x] 取消：`CANCEL_REQUESTED` 落库，Worker 在节点边界停止且不产生新 LLM 调用（迁移现契约 C1~C7）
- [x] 租约超时清扫与接管：取消意图 → `CANCELLED`（不重跑）；可重试 → `QUEUED`（`attempt+1`）并重新入队；重试耗尽 → `LOST`（`FOR UPDATE SKIP LOCKED` 原子接管，Worker 每 30s 清扫）
- [x] 排队超时收口（P0-2）：`QUEUED` 且 `timeout_at` 已过 ⇒ 清扫置 `TIMED_OUT`（不执行、不占额度）；孤儿 QUEUED 由下一轮领取自动回收
- [x] 崩溃接管：Worker 进程崩溃后由租约清扫接管；图内异常 → `FAILED`（P3-A 已覆盖）
- [x] 启动维护按执行模式分流（P3-B 补丁）：`inprocess` 启动才标记失联任务 `LOST`；`queue` 模式下 API 重启不触碰活跃任务（RUNNING 归 Worker 心跳/租约、QUEUED 归 Redis 队列，接管只走 `sweep_stale_runs`）
- [x] 并发限制：全局闸（API 配置上限）+ 单用户并发闸（`DR_MAX_USER_CONCURRENT`，P4-B）；**P0-3 起准入与插入同事务原子判定**（多实例并发不会突破）
- [x] 单 run 预算闸在 Worker 节点边界生效（`budget_limit_cny` → `budget_used_cny` 每步回写；超限 `stop_reason=budget_exceeded`）
- [x] 终局原子落库（P0-6）：状态迁移 + 终局事件 + 产物**同一事务**（`RunStore.finalize_run`）；迁移失败（已被清扫 / 强制收口抢先）整体回滚，不产生「SUCCEEDED 但报告缺失」或「状态未迁移但产物已写」的半成品
- [x] 准入原子化（P0-3）：`create_run_admitted` 同一事务内检查月度预算 / 全局并发 / 单用户并发 / 每日次数（`pg_advisory_xact_lock` 全局 + 用户锁），失败整体回滚；真实 PG 并发竞争测试在 CI `infra` 执行
- [x] 用户级每日次数与全局月度预算闸（P4-B：429 `quota_exceeded`；查询 / 导出不受影响）
- [x] 运行档位（P0 profile 固化）：`quick/standard/deep` 由服务端固定跳数 / 子问题 / token / 模型 / 超时 / 单次预算（需求 10 §5.6）；请求体同名字段一律忽略并记入 `runs.request.ignored_overrides`；执行器经 contextvar 运行作用域读取，不再改全局 `config`
- [ ] 用户级 / 全局成本闸 —— P4
- [ ] 不同步做断点续跑（首发只承诺「任务不丢」，见需求 10 §5.5）

### 3.4 身份与权限

- [x] 注册 / 登录 / 登出 / 会话过期 / 用户封禁（CLI）/ 邀请码（一次性、可过期、可撤销）—— P4-A；**P1-10 会话治理**：会话列表（session_id / last_seen / IP / UA，当前标记）、终止单会话（终止他人需重输密码，ASVS 7.5.2）、退出其他设备；last_seen 节流刷新；可选空闲超时 `DR_SESSION_IDLE_SECONDS`（默认 0 = 仅绝对超时，口径登记）
- [x] 密码重置（P1-10，管理员协助形态）：`password_reset_tokens` 只存 SHA-256、单次原子消费、30 分钟过期、兄弟 token 互斥；`POST /api/auth/reset` 同事务完成「消费 + 改密 + 吊销全部会话 + 清理 token」；管理员 CLI `create-reset-token --email`；⚠️ 邮件自助通道待有 SMTP/SMS 后接入（接口已按该形态设计）
- [x] 密码哈希 Argon2id；会话 httpOnly + SameSite；`DR_COOKIE_SECURE=true` 时加 Secure（**P0-9：staging/生产启动硬校验要求为 true**，不允许带 `Secure=false` 启动）
- [x] CSRF 防护：双提交 Cookie（写操作校验 `X-CSRF-Token`）—— P4-A
- [x] 登录与提交接口限流：**P1-2 起配置 Redis 时为滑动窗口（Lua 原子、多实例共享）**，未配置回落进程内固定窗口；登录双维度（IP + 账号哈希，防定向撞库）；**P0-10 起优先 `DR_TRUSTED_PROXY_CIDRS` / `DR_PROXY_HOPS`：仅可信代理 CIDR 内的直连对端才采信 XFF / `X-Forwarded-Proto`（`DR_TRUST_PROXY=true` 仅作无 CIDR 时的历史兼容，不再用于生产）**；Redis 抖动 **fail-open** 并记 `ratelimit_redis_error`（限流是纵深，非唯一安全边界）
- [x] 运行类接口归属校验：鉴权开启时非本人一律 404；管理层走 CLI（不暴露 HTTP 管理面）
- [x] 越权负向测试：查询 / 取消 / 导出 / SSE 订阅他人 `run_id` 全部 404 且不泄露存在性
- [x] 密钥与账号不进日志（错误载荷不回显 password / token；FastAPI 默认不记录请求体）
- [x] 安全审计日志（P1-5）：迁移 0007 `audit_logs`（**append-only**，无密码/token/PII，邮箱只记 `sha256[:16]`，actor 无外键保留线索）；请求级 `X-Request-ID` 关联。**事件最小集**：`register_success` / `login_success` / `login_failed` / `logout` / `password_changed` / `account_deletion_requested` / `csrf_failed` / `authz_denied` / `input_blocked` / `admin_*`（CLI 动作）；CLI `audit-list` 查看。⚠️ 防篡改（哈希链）与集中化外送（SIEM）留 P2；moderation 领域记录仍在 `moderation_records`

### 3.5 内容安全与隐私

- [x] 输入侧（P7-A 部分）：超长输入限制（topic 1000 / instructions 2000）与文件类型 / 大小限制（P6-A）；
      敏感内容预检（规则词表 `DR_MODERATION_BLOCKLIST`，命中即拒并留痕）
- [x] 注入确定性防护（P2-1a，OWASP LLM01:2026 口径）：外部内容统一剥离不可见 Unicode（搜索/arXiv/RAG 摄入与检索两侧）；输入显式注入模式预检（中英文窄口径）；**输出泄漏过滤**（系统提示有限标记集合，命中按 P0-4 脱敏 + flagged）；`research_engine/net/safe_fetch.py`（resolve→全地址公网校验→禁重定向→pin IP→限时限量）作为未来 URL 抓取的唯一入口；prompts 未改动（W8 基线不受影响）
- [ ] 注入深度防护（P2-1b，另行评估）：spotlighting 提示词标注、guardrail 模型、Rule of Two 人工确认 —— 涉及核链提示词变更，需先评测基线影响
- [ ] 恶意 URL / 文件处理（P7-B）：与审核 provider / 文件扫描联动
- [x] 审核 provider 抽象（P2-5a）：`web/backend/moderation_providers.py` 统一接口 `ModerationProvider.scan(text) -> ModerationResult`；默认 `local_rules`（`DR_MODERATION_BLOCKLIST`）；`DR_MODERATION_PROVIDER` 选择实现，未知/未实现（如 `aliyun`）或 provider 异常记 WARNING 回退 `local_rules`；**P0-3 起降级不再等于放行**：结果带 `degraded` / `failure_reason` / `policy`，按 `DR_MODERATION_DEGRADED_POLICY`（默认 `quarantine`）隔离待审 / `fail_closed` 阻断 / `allow`（仅内测且必须留痕告警）；留痕 `moderation_records.detail.provider` 供审计/申诉溯源；egress 快照动态记录当前 provider；**本 PR 不引入任何外部 SDK / 网络调用**（阿里云等实现留待接入阶段）
- [x] 输出侧（P7-A 部分）：命中词表 ⇒ `moderation_status=flagged` + 审核记录；**P0-4 起自动拦截**：事件流 / 落库前按**唯一一次不可变决定**（`evaluate_output` → `output_decision` 记录）递归脱敏全部正文键、导出 403 `output_under_review`、原文只留 `run_artifacts` 供 CLI `run-report` 复核；终局、审核状态与决定证据**同事务**落库（关闭「终局已落但审核未落」窗口）
- [x] 申诉/复核状态机（P2-5b）：迁移 0014 `moderation_appeals`（同一 run+用户唯一；`pending→reviewing→accepted/rejected`；`sla_due_at` 由 `DR_APPEAL_SLA_HOURS` 默认 72h 生成）；API `POST /api/moderation/appeal` 建单 + `GET /api/moderation/appeals` 查本人状态；**accepted ⇒ run 置 `cleared`（迁移 0015 补齐 CHECK 取值，导出闸放行）、rejected 维持 `flagged`**；**P0-5 起决策为单事务**：必须由领取者决策（他人 claim 后不可越权），申诉行 / run 状态 / `moderation_records`（append-only 证据）/ `audit_logs` 同生共死；超期未决进入 `appeal_sla_overdue` 告警；CLI `appeal-list` / `appeal-claim` / `appeal-decide`；`moderation_appeals` 纳入 180 天保留期清理
- [ ] 敏感结果拦截与模型输出标识 —— P7-B（含接入有资质的审核服务）
- [x] 审核记录留存与管理侧入口（P7-A：`moderation_records` + CLI `moderation-list` / `delete-user`）
- [x] 处置链路：申诉入口（`POST /api/moderation/appeal`）已可用；**P0-5 起带 `run_id` 的申诉校验归属 / 标记状态 / 防重复**（非本人 404、未标记 409 `appeal_not_applicable`、重复 409 `appeal_duplicate`）；封禁（CLI `ban-user`）与账号删除（API/CLI）可演练
- [x] 隐私政策 / 用户协议草案（`docs/legal/`，API 可读）；⚠️ **法务确认与公示仍待 L3-C**
- [x] 用户注销已实现（验密 + CSRF；**P0-7 起 durable outbox**：同事务登记台账 + outbox + 删账号/会话，Qdrant 清理由 Worker 带退避重试直至**验证归零**；耗尽 `abandoned` 会告警，CLI `deletion-list` / `retry-deletion` 可查可重试；任务匿名保留）
- [x] 报告保存期限的**到期自动清理**（P2-2）：`web/backend/retention.py` 政策表（事件 30 天 / 终态运行与产物 90 天 / 用量账本与审核留痕与审计日志 180 天，环境变量可调），Worker 每日执行 + CLI `retention-run [--dry-run]`；批量 `ctid + LIMIT`（5000 行/批，最多 20 批/表/日）避免长事务；**误删护栏**：单表候选量 > `max(1000, 50% × 表总量)` 时中止该表并写 `retention_guardrail` 审计；每表执行写 `retention_purge` 审计（dry-run 同样留痕）；S3 报告由桶生命周期 90 天删除（对象层，双轨）
- [x] 日志与错误载荷脱敏（不回声密码；登录失败统一 401；密钥不进日志有回归测试）
- [x] 上传文件按用户作用域隔离存放与检索（P5/P6-A；不与公共知识混存）；**P0-8b**：原文只存隔离区，处理完成即删除；账号注销与 90 天保留期都会清理（向量 + 文件）

### 3.6 可观测性与告警

> P8-A 已落地「可判定」：`GET /api/metrics`（进程内 HTTP/SSE 计量 + 任务库聚合，无 PII）
> 与 `GET /api/ops/alerts`（`DR_ALERT_*` 阈值）。**P0-9 起两者均有应用层鉴权**：
> 配置 `DR_OPS_TOKEN` 后必须携带 `X-Ops-Token`（常量时间比较）；staging/生产未配置
> 一律 401（fail-closed）—— 反向代理限制来源只作纵深，不再是唯一边界。
> **触达**（IM/邮件）与中心化指标（Prometheus/多实例）属 P8-B。

| 指标 | 阈值（待定） | 告警触达 |
|------|-------------|---------|
| API 错误率（5xx / 4xx 分离） | 待定 | 待定 |
| 队列等待时间 / 积压 | 待定 | 待定 |
| 任务成功率 / 超时率 | 待定 | 待定 |
| provider 错误率（模型 / 搜索） | 待定 | 待定 |
| 单用户成本 / 全局成本 | 待定 | 待定 |
| Worker 心跳 / 存活 | 待定 | 待定 |
| PostgreSQL 连接数 / 慢查询 | 待定 | 待定 |
| Qdrant 查询错误率 | 待定 | 待定 |
| SSE 连接数 | 待定 | 待定 |
| 备份成功状态 | 待定 | 待定 |

- [x] 告警触达值班人（IM/邮件至少一条链路）
- [x] 中心化观测（P1-8，OTel）：`OTEL_EXPORTER_OTLP_ENDPOINT` ⇒ traces+metrics 走 OTLP；`DR_METRICS_PROMETHEUS=true` ⇒ API 暴露 `/metrics`；resource 属性 `service.name`/`service.version`/`deployment.environment.name`；FastAPI+httpx 自动埋点；`gen_ai.client.token.usage`（input/output 拆分）与 `gen_ai.client.operation.count`（接 usage sink，**不落 prompt**）；后台任务独立根 span；默认全关（零行为变化）
- [x] 容量标定（P2-7，手动不进 CI）：`tools/loadtest/locustfile.py`（提交 / SSE 尾随 / 探针混合，配额与限流拒绝按预期计入）+ `tools/loadtest/README.md`（`DR_LOADTEST_GRAPH=1` 假图零 LLM 费用）+ `docs/operations/capacity-model.md`（连接预算公式、存储增长上界、测量流程、**实测基线表待回填**）
- [x] 告警外送（P2-6）：Worker 每 `DR_ALERT_CHECK_SECONDS`（默认 60s）判定 store/queue 侧告警（5xx 属 API 进程指标，仅 API 展示）→ `alert_states` 指纹收敛（firing 首次即发 / 冷却 `DR_ALERT_REPEAT_MINUTES` 默认 30min 内不重发 / 消失必发 resolved）→ `alert_deliveries` 外送队列；`DR_ALERT_WEBHOOK_URL` 支持飞书 / 钉钉 / 企业微信 / 通用 JSON（未配置时只维护状态，零外呼）；失败指数退避（30s×2ⁿ，上限 30min），`DR_ALERT_MAX_ATTEMPTS`（默认 5）次置 `given_up=true` 放弃（CLI `retry-alert` 复位重试）；payload 只带告警元数据（不含用户内容）；CLI `alerts-list` / `alert-deliveries`
- [x] Worker 注册表与心跳（P1-3）：`workers` 表（active→draining→stopped、in_flight、current_run_id）；心跳 best-effort（失败自动重注册）；SIGTERM 先 draining、当前任务收口后置 stopped；`/api/metrics.workers_live` + 告警 `worker_heartbeat_missing`
- [ ] 错误日志带 `run_id` / `user_id` 关联，便于定位
- [ ] 现有限制如实保留：协作式取消/超时是语言级限制（Python 线程无法强杀）；节点内部挂死时硬截止只保证传输层收口（见 ADR-0008 与看板 P1 说明）

### 3.7 成本与预算

- [ ] 成本模型口径写明：现行 ¥0.79~0.92/轮是**按最贵 output 单价计的上界估算**（`runner.py:74`），不是商业成本模型
- [x] 预算闸落地（P3-B / P4-B）：单次 `DR_RUN_BUDGET_CNY=¥1.50` 在 Worker / 进程内执行器的节点边界生效；全局月度 `DR_MONTHLY_BUDGET_CNY`（默认 ¥1,500）达 100% 熔断新任务；单用户每日 `DR_DAILY_RUNS_PER_USER=1`
- [x] 超限行为定义并留痕：预算停止 `stop_reason=budget_exceeded`（不伪装成 `TIMED_OUT`）；配额拒绝 429 `quota_exceeded`；查询 / 导出 / 管理保持可用
- [ ] 月度 80% 预警通知（当前只有 100% 熔断；阈值告警属 P8 告警体系）
- [x] `usage_ledger` 逐调用记账（P1-4）：LLM / embedding / 搜索逐次一行（重试按 `attempt` 区分；`cost_source` 标注精度：`estimate` 本地价格表上界 / `per_call` 按次未建模 / `provider` 供应商实报）；账本无 prompt/PII；CLI `usage-summary` 按 run/时间窗口汇总（先对请求数再对钱）
- [ ] 与 provider 账单定期核对（对账流程人工执行；供应商实报成本接入后写 `cost_source=provider`）—— P8

### 3.8 备份与恢复演练

- [x] 备份脚本化（P8-A：`tools/backup.sh`，pg_dump + `pg_restore -l` 可读校验 + 保留 N 份）
- [x] 恢复演练脚本化并**已跑通**（P8-A：`tools/restore.sh`；CI `infra` job 每次提 PR 真实执行「备份→清库→恢复」，
      并用种子数据 before/after 相等 + 恢复后结构断言双向验证）
- [ ] 云上 staging / 生产的定期备份调度与保留策略（P8-B：依赖云账号）
- [ ] 演练记录按 §5 模板落档（当前证据为 CI 日志与本地实跑记录）

### 3.9 发布与回滚

- [ ] 发布流程：迁移 → 新版本部署 → 冒烟 → 放量；数据库迁移与旧版本兼容
- [ ] 回滚：应用版本回滚路径 + 迁移回滚路径各自验证过
- [ ] 灰度范围与观察窗口（L3-C 前）
- [ ] 发布检查单：配置 diff、密钥、CORS、反向代理、迁移、备份点

### 3.10 合规准入

- [ ] ICP 备案：主体（**当前为个人主体**；公开运营前置为企业主体）、域名实名、云厂商接入审核 → 属地管局审核
  - 时间按 **1~4 周预留**，以接入商与属地管局反馈为准，不写死承诺
- [ ] 公安联网备案：网络正式联通之日起 **30 日内**办理
- [ ] 生成式 AI 相关手续确认（是否面向境内公众、是否具舆论属性）：
  - [ ] 向属地网信部门与云厂商确认：需要的是相关 AI 服务登记/备案、算法备案、安全评估，还是仅使用已备案模型并履行展示与管理义务
  - [ ] 确认结论写入本文件「落档记录」
- [ ] 已备案模型信息公示：在显著位置或产品详情页展示所使用的已备案服务、模型名称与备案号
- [ ] 数据出境评估：L3-A/B 默认不发送用户内容出境；Tavily / Langfuse Cloud / arXiv / Semantic Scholar 默认关闭，恢复须独立评审（需求 10 §3.1 第 16 项 + 本文件 §4 登记）
- [ ] 商用供应商授权确认（模型 / 搜索 / 可观测）

**合规口径提醒（不得简化）**：
1. 邀请制**不是合规豁免**，而是第一阶段的范围控制策略；
2. 「模型已备案」≠「应用已合规」≠「ICP 备案」≠「公安联网备案」，四件事分别办理；
3. 下列为公开依据（正式动作前请以属地部门最新要求为准）：
   - 《生成式人工智能服务管理暂行办法》：https://www.cac.gov.cn/2023-07/13/c_1690898327029107.htm
   - 已备案生成式人工智能服务信息（含公示要求）：https://www.cac.gov.cn/2024-04/02/c_1713729983803145.htm
   - ICP 备案流程（工信部属地管局审核）：https://xzca.miit.gov.cn/zwgk/zfxxgkzl/art/2024/art_d889ef822e384fd38e8a6be6818b32bb.html
   - 公安联网备案要求：https://xzfg.moj.gov.cn/front/law/detail?LawID=498
4. **本清单不构成法律意见**；结论以属地网信部门、管局、接入商与云厂商的书面答复为准。

## 4. 数据流向登记表（现状初始登记，待逐项确认）

> 用途：回答「用户的什么问题、什么内容，会流向哪家服务商、存放在哪里、留多久」。
> 状态列初值来自代码事实；「待确认」项在 P7 前必须落档。
> **P1-9 起每个 run 创建时固化数据流向快照**（`runs.request.egress`：模型/provider/端点域/策略版本/境外开关，不含密钥），
> 历史任务可逐条解释「当时数据发给了谁」；快照随 `/api/research/{run_id}` 与导出 JSON 展示。
> **政策基线（2026-09-24，推荐基线 v2）**：L3-A/B 默认不发送用户内容至境外服务；
> Tavily、Langfuse Cloud、Semantic Scholar、arXiv 默认关闭，恢复须独立功能开关 + 数据流向提示 + 重新评审。

| 供应商 / 服务 | 用途 | 可能发送的数据 | 地域 / 留存 | 状态 |
|--------------|------|---------------|------------|------|
| 阿里云百炼 DashScope | LLM 推理 + Embedding（`text-embedding-v3`） | 研究问题、检索片段、中间输出、报告草稿 | 国内；以阿里云条款为准 | 待确认商用授权与数据条款；生产端点用业务空间专属域名 |
| 博查（bocha） | 网络搜索（默认且当前唯一） | 检索查询词 | 国内 | 待确认商用授权；L3-B 前补国内第二源或登记为已知单点 |
| Tavily | 网络搜索（备用/降级源） | 检索查询词 | 境外 | **L3-A/B 默认关闭**；启用须独立评审 |
| arXiv | 学术检索（`ENABLE_ARXIV`） | 检索查询词（源自用户研究问题，等同出境） | 境外 | **L3-A/B 默认关闭**；恢复须独立评审并提示数据流向 |
| Semantic Scholar | 可选的引用数后处理 | 检索查询词 | 境外 | **L3-A/B 默认关闭**；启用须独立评审 |
| Langfuse Cloud | 可观测（trace / 成本） | 提示词与输出（`truncate_len=4000`；`mask_sensitive` 默认 false） | 境外（cloud.langfuse.com） | **L3-A/B 默认关闭**；替代 = PostgreSQL 运行统计 + 结构化日志；自托管另行评估运维负担 |
| Qdrant | 向量库（默认 `127.0.0.1:6333`） | 上传文档分块与向量 | 取决于部署 | 自托管可控；L3 改为多租户隔离 |
| 新增（L3）：PostgreSQL / Redis / 对象存储 | 权威状态 / 队列 / 报告 | 用户资料、任务、事件、报告、上传文件 | 单云单地域（国内，待填） | 部署后登记 |

## 5. 恢复演练（模板）

演练目标：证明「备份可恢复、数据可用、隔离有效」。

```text
1. 记录备份点（时间戳 / 备份文件位置 / 数据版本）
2. 在 staging 删除数据库与对象存储中的报告
3. 从备份恢复数据库与对象存储
4. 验证：任务列表可查、报告可下载、事件时间线与导出正常
5. 越权核验：用户 A 无法访问用户 B 的任务与报告
6. 记录耗时、失败项与改进项
```

| 日期 | 执行人 | 备份点 | 步骤 1-6 结果 | 发现的问题 | 结论 | 关联工单 |
|------|--------|--------|--------------|-----------|------|---------|
| 待填 | 待填 | 待填 | 待填 | 待填 | 待填 | 待填 |

> **P0-7 追加步骤（恢复后必做）**：比对 `account_deletions` 台账与运维侧备份点之后的注销请求记录，
> 对备份恢复后「复活」的用户重放删除（当前为人工 runbook；自动重放列 P1）。
> 台账无 PII，但若恢复点早于注销时间，台账行也可能回滚 —— 以运维侧的注销请求记录为准补齐。

## 6. 落档记录（合规确认 / 供应商答复）

| 日期 | 事项 | 来源（部门 / 云厂商 / 供应商） | 结论摘要 | 证据（文件 / 邮件 / 截图路径） |
|------|------|-------------------------------|---------|------------------------------|
| 待填 | 生成式 AI 手续确认 | 待填 | 待填 | 待填 |
| 待填 | ICP 备案进度 | 待填 | 待填 | 待填 |
| 待填 | 数据出境 / 供应商条款 | 待填 | 待填 | 待填 |

## 7. 附录（配置样例与操作手册）

### 7.1 Nginx SSE 反向代理样例

```nginx
location /api/ {
    proxy_pass http://127.0.0.1:8000;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;

    # SSE 专项（必须）
    proxy_buffering off;
    proxy_cache off;
    proxy_read_timeout 3900s;   # > 默认 run_timeout 3600s + 余量
    proxy_send_timeout 3900s;
    chunked_transfer_encoding on;

    # P0-8a 上传：在代理层先于应用拒绝超大/慢速请求体
    client_max_body_size 12m;   # = DR_RAG_MAX_FILE_MB(10) + multipart 余量
    client_body_timeout 60s;
}
```

> 上线前用真实浏览器验证 15s 心跳（`agui.HEARTBEAT_SECONDS`）不中断；反向代理层不得再叠加压缩。
> 最终以内网网关实际选型为准；此样例用于验收「代理配置是否正确」。

### 7.2 环境变量与密钥

- 新增变量清单见需求 10 §5.13；敏感值（数据库 DSN / Session 密钥 / provider key）只从密钥服务或部署平台注入，禁止写入镜像、仓库与日志。
- 启动自检：必填项缺失时 fail fast（P1 实现），不允许带默认密钥启动。

### 7.3 备份与恢复命令基线

```text
备份：pg_dump -Fc "$DR_DATABASE_URL" -f dr-<date>.dump
      对象存储：桶版本化 + 生命周期（与需求 10 §3.1 第 15 项保存期限一致）
恢复：pg_restore --clean --if-exists -d "$DR_DATABASE_URL" dr-<date>.dump
演练：按 §5 步骤执行并记录，断言「任务可查 / 报告可下 / 越权被拒」
```

> 最终命令以云厂商托管数据库的备份方案为准；本文件固定的是**演练步骤与断言**，不是具体工具。

### 7.4 发布检查单

> 逐步操作手册：`docs/operations/release-runbook.md`（P2-8；含回滚决策树与记录模板）。

- [ ] 迁移已评审：前向兼容（旧 API 进程可读新表结构）
- [ ] 备份点已生成并校验可读
- [ ] 配置 diff 已核对（CORS / 预算 / 并发 / 境外服务开关）
- [ ] 新版本部署 → readiness 通过 → staging 冒烟（提交 / 进度 / 取消 / 导出）
- [ ] 观察窗口内错误率、队列等待、worker 心跳正常
- [ ] 回滚路径已验证（应用版本回滚 + 数据回退走备份恢复）—— 流程见 runbook §6；**应用回滚演练未执行前不得视为已验证**（runbook §9 有状态登记）

### 7.5 告警阈值初始候选（待压测校准）

> 压测入口：`tools/loadtest/`（P2-7）；阈值校准流程与连接预算见
> `docs/operations/capacity-model.md` §3/§4。基线数值回填前，下表按保守值执行。

| 指标 | 初始候选阈值 | 说明 |
|------|-------------|------|
| API 5xx 比例 | > 2% 持续 5 分钟 | 触发即人工介入 |
| 队列等待时间 | > 5 分钟 | L3-A/B 规模下的明显异常 |
| 任务失败率 | > 20% 持续 30 分钟 | 排除取消 / 超时 / 预算停止 |
| 任务超时率 | > 20% 持续 30 分钟 | 单独告警，不并入失败率 |
| 预算月度消耗 | ≥ 80% | 预警（§3.7） |
| Worker 心跳 | 缺失 > 2 个周期（120s） | 检查租约接管 |
| 备份任务 | 失败即告警 | 未验证的备份视为不存在 |
| SSE 连接数 | 接近并发上限持续 10 分钟 | L3-C 前仅记录不告警 |

## 8. 变更记录

| 日期 | 变更说明 |
|------|---------|
| 2026-09-24 | 初始草稿：G1~G8 准入 Gate、环境矩阵、十类检查清单、数据流向初始登记、恢复演练与合规落档模板 |
| 2026-09-24 | 同步需求 10 推荐基线 v2：§3.7 预算闸改为 L3-A ¥1,500/月、L3-B ¥2,000/月 + 单次 ¥1.50 + `budget_exceeded`；§3.10 主体改为个人主体口径；§4 境外服务标记默认关闭与恢复评审条件 |
| 2026-09-24 | 新增 §7 附录：SSE 反代样例、密钥纪律、备份/恢复命令基线、发布检查单、告警阈值初始候选（对齐需求 10 §5.9~§5.13） |
| 2026-09-24 | P1 第一批骨架落地同步：§3.1 迁移项与 §7.4 回滚项改为「只前向 + 幂等；回退走备份恢复」口径；本地 compose 实跑（迁移两次 `applied=0`、ready=ok）与探针证据见 `docs/project-status.md` |
| 2026-09-26 | P2-C 持久化接线同步：§3.2 建表清单已覆盖 `runs` / `run_events` / `run_artifacts`（迁移 0001/0002）；readiness 的 PostgreSQL 探针升级为真实 `SELECT 1`；重启后查询 / 导出 / SSE 回放已可用（云上 staging 仍待部署） |
| 2026-09-26 | P3-A Worker/队列同步：§3.1 容器项与 §3.3 任务运行项勾选（队列模式 API / Worker 租约心跳 / 幂等 / 超时 / 取消）；租约清扫与重试、单用户并发、成本闸明确留待 P3-B / P4 |
| 2026-09-26 | P3-B 清扫/重试/预算同步：§3.3 勾选「租约超时清扫与接管」「崩溃接管」「单 run 预算闸」；用户级/全局成本闸与单用户并发仍留 P4 |
| 2026-09-26 | P4-A 账号同步：§3.4 勾选注册/登录/会话/封禁/邀请码（Argon2id、httpOnly+CSRF、归属校验、越权负向测试）；密码重置与接口限流明确留 P4-B |
| 2026-09-26 | P4-B 配额同步：§3.3 勾选并发限制与用户级/全局预算闸；§3.4 限流落地（标注多实例需迁 Redis）；§3.7 预算闸与超限行为勾选，80% 预警与对账留 P8 |
| 2026-09-26 | P5-A RAG 隔离同步：§3.2 勾选「检索按作用域强制过滤」；HTTP 上传面（携带当前用户）随 P6 前端产品化落地 |
| 2026-09-26 | P6-A 前端产品化同步：§3.2 勾选「RAG 上传面」（类型白名单 + 大小上限 + 按用户打标）；前端账号壳/历史/配额/上传已落地，内容安全链路仍待 P7 |
| 2026-09-26 | P7-A 内容安全与隐私同步：§3.5 勾选输入预检/长度限制/审核记录/申诉/注销/脱敏/上传隔离；**输出自动拦截、Prompt Injection 深度防护、审核服务接入、期限到期清理**明确留 P7-B/P8（已含隐私政策草案，法务确认待 L3-C） |
| 2026-09-26 | P8-A 可观测与恢复演练同步：§3.6 增加「可判定已落地」说明（`/api/metrics`、`/api/ops/alerts`；触达与中心化留 P8-B）；§3.8 勾选备份脚本化与恢复演练（CI 每次 PR 真实执行） |
| 2026-09-27 | P3-B 补丁：API 启动维护按执行模式分流（`main.py::_startup_store_maintenance`）—— 修复 queue 模式下 API 重启误将 Worker 的 RUNNING/QUEUED 标记为 `LOST` 的跨服务破坏；§3.3 增补对应勾选；新增 `tests/test_startup_maintenance.py`（本机 568 收集 = 543 通过 + 25 跳过，ruff 全过） |
| 2026-09-27 | P0 profile 固化同步：§3.3 勾选运行档位（服务端固定底层参数 + 快照留痕 + contextvar 运行作用域）；新增 `web/backend/profiles.py` / `research_engine/runtime_profile.py`、`runs.request.profile` 快照、前端档位选择器；新增 `tests/test_profiles.py`（本机 575 收集 = 550 通过 + 25 跳过，ruff / tsc / vite build 全过） |
| 2026-09-27 | P0-6 终局原子落库同步：§3.3 勾选终局原子（`RunStore.finalize_run` 单事务：状态 + 事件 + 产物；迁移失败整体回滚）；`persistence.persist_terminal` / `persist_forced` 返回是否完成迁移，Runner/Worker 据此跳过输出标记；新增 `tests/test_terminal_atomic.py`（本机 581 收集 = 555 通过 + 26 跳过，ruff 全过） |
| 2026-09-27 | P0-4 输出闸同步：§3.5 输出侧改为「自动拦截」——`apply_output_gate` 发帧/落库前脱敏 + 审核状态与终局同事务 + 导出 403 `output_under_review` + 历史回放强制脱敏 + CLI `run-report`；新增 `tests/test_output_gate.py`（本机 587 收集 = 561 通过 + 26 跳过，ruff / tsc / vite build 全过） |
| 2026-09-27 | P0-5 申诉校验同步：§3.5 处置链路勾选归属 / 状态 / 防重复（带 `run_id` 的申诉）；新增错误码 `appeal_not_applicable` / `appeal_duplicate` 与 `store.has_appeal`（本机 588 收集 = 562 通过 + 26 跳过，ruff 全过） |
| 2026-09-27 | P0-3 准入原子化同步：§3.3 并发与预算闸改为同事务原子判定（`RunStore.create_run_admitted` + `pg_advisory_xact_lock`）；真实 PG 并发竞争测试接入 CI `infra`（本机 589 收集 = 562 通过 + 27 跳过，ruff 全过） |
| 2026-09-27 | P0-2 队列可靠性同步：§3.2/§3.3 —— 派发权威迁到 PostgreSQL（`claim_next_queued` + `FOR UPDATE SKIP LOCKED`），Redis 降级为可选唤醒信号；排队超时收口 `TIMED_OUT`；队列深度以 `count_queued` 为准；真实 PG 并发领取测试接入 CI `infra`（本机 593 收集 = 565 通过 + 28 跳过，ruff 全过） |
| 2026-09-27 | P0-9 启动硬校验同步：§3.1 配置分层勾选（`DR_ENV` fail fast）、§3.4 Cookie 行更新；compose staging 默认 `DR_COOKIE_SECURE=true` 并在文件头注明自检要求；production 拒明文 HTTP（400 `https_required`）；新增 `tests/test_startup_validation.py`（本机 599 收集 = 571 通过 + 28 跳过，ruff 全过） |
| 2026-09-27 | P0-7 注销 durable outbox 同步：§3.5 注销行更新（台账 + outbox + 验证归零 + 告警 + CLI）；§5 恢复演练追加「重放删除台账」步骤；迁移 0005 与结构断言接入 CI `infra`；新增 `tests/test_deletion_outbox.py`（本机 604 收集 = 575 通过 + 29 跳过，ruff 全过） |
| 2026-09-27 | P0-8a 上传硬化同步：§3.2 上传面更新（流式 + 三重校验 + 内容寻址 + 解析限额 + 限流）；§7.1 反代样例补 `client_max_body_size` / `client_body_timeout`；新增错误码 `unsupported_file_type` / `payload_too_large` / `document_limit_exceeded` 与 `tests/test_upload_hardening.py`（本机 612 收集 = 583 通过 + 29 跳过，ruff / tsc / vite build 全过） |
| 2026-09-27 | P0-8b 异步摄取管线同步：§3.2/§3.5 —— 隔离区 + 202 登记 + Worker 状态机（含可选 ClamAV）、`DELETE /api/rag/docs`、90 天保留期清扫、注销 outbox 联动清文件；compose api/worker 共享 `rag_quarantine` 卷；迁移 0006 与结构断言接入 CI `infra`；新增 `tests/test_ingestion_pipeline.py`（本机 623 收集 = 593 通过 + 30 跳过，ruff / tsc / vite build 全过） |
| 2026-09-27 | P1-5 安全审计日志同步：§3.4 增补审计勾选与事件最小集（ASVS V16 口径）；迁移 0007 + 结构断言接入 CI `infra`；新增 `tests/test_audit_logs.py`（本机 628 收集 = 597 通过 + 31 跳过，ruff 全过） |
| 2026-09-27 | P1-1 幂等请求指纹同步：§3.3 幂等键行更新（同键不同载荷 409）；迁移 0008 + 结构断言接入 CI `infra`；新增 `tests/test_idempotency_conflict.py`（本机 634 收集 = 603 通过 + 31 跳过，ruff 全过） |
| 2026-09-27 | P1-3 Worker registry 同步：§3.1 readiness 项勾选（队列模式要求活跃 worker）、§3.6 增补注册表/心跳；迁移 0009 + 结构断言接入 CI `infra`；新增 `tests/test_worker_registry.py`（本机 640 收集 = 608 通过 + 32 跳过，ruff 全过） |
| 2026-09-27 | P1-2 分布式限流同步：§3.4 限流行更新（Redis 滑动窗口 + 双维度 + 可信代理 + fail-open）；compose 增 `DR_TRUST_PROXY`；真实 Redis 原子性测试接入 CI `infra`；新增 `tests/test_distributed_ratelimit.py`（本机 646 收集 = 613 通过 + 33 跳过，ruff 全过） |
| 2026-09-27 | P1-10 会话治理同步：§3.4 会话/密码重置行更新（会话列表与远程终止、重置 token 单次/30min/管理员发放）；迁移 0010 + 结构断言接入 CI `infra`；新增 `tests/test_session_governance.py`（本机 654 收集 = 620 通过 + 34 跳过，ruff 全过） |
| 2026-09-27 | P1-4 usage ledger 同步：§3.7 逐调用记账勾选（成本来源标注 + CLI 汇总）；迁移 0011 + 结构断言接入 CI `infra`；新增 `tests/test_usage_ledger.py`（本机 661 收集 = 626 通过 + 35 跳过，ruff 全过） |
| 2026-09-27 | P1-9 provider/egress 快照同步：§4 数据流向登记表增「run 级快照」说明；`web/backend/egress.py` + 快照接口/导出展示；新增 `tests/test_egress_snapshot.py`（本机 664 收集 = 629 通过 + 35 跳过，ruff 全过） |
| 2026-09-27 | P1-7 Qdrant payload index 同步：§3.2 检索过滤行更新（`is_tenant` 索引 + 幂等补齐）；新增 `tests/test_qdrant_indexes.py`（本机 667 收集 = 632 通过 + 35 跳过，ruff 全过） |
| 2026-09-27 | P1-8 OTel 同步：§3.6 中心化观测勾选（OTLP/Prometheus 双路径 + GenAI 指标 + 后台任务 span，默认关）；compose 增 OTLP/Prometheus 透传；依赖入 lock；新增 `tests/test_otel.py`（本机 672 收集 = 637 通过 + 35 跳过，ruff 全过） |
| 2026-09-27 | P1-6 对象存储同步：§3.2 对象存储项勾选（MinIO 私有桶 + 90 天生命周期 + 元数据化 + PG 双轨回落 + S3 失败兜底 + readiness 探针）；迁移 0012 + 结构断言接入 CI `infra`；依赖 boto3 入 lock；新增 `tests/test_object_storage.py`（本机 678 收集 = 643 通过 + 35 跳过，ruff / compose config 全过） |
| 2026-09-27 | P2-1a 注入确定性防护同步：§3.5 增补确定性层勾选（不可见字符剥离 / 输入预检 / 输出泄漏过滤 / safe_fetch），深度防护（spotlighting、guardrail）显式记为 P2-1b；新增 `tests/test_injection_defense.py` + `tests/test_safe_fetch.py`（本机 692 收集 = 657 通过 + 35 跳过，ruff 全过） |
| 2026-09-27 | P2-2 数据保留自动清理同步：§3.7 报告到期自动清理项勾选（政策表 + Worker 每日 + CLI + dry-run + 误删护栏 + 审计留痕）；新增 `tests/test_retention.py`（FakeStore 政策用例 + 真实 PG `purge_before` 批量子句/级联契约，已加入 CI `infra` job）；本机 698 收集 = 662 通过 + 36 跳过，ruff 全过 |
| 2026-09-27 | P2-3 连接池与慢查询同步：§3.2 连接池项勾选（`psycopg_pool` + `statement_timeout` + `_TimedCursor` 慢查询 WARNING（不含参数）+ `DR_PG_*` 可调 + `RunStore.close()`）；依赖 `psycopg-pool` 入 lock；新增 `tests/test_pg_pool.py`（配置解析 + 真实 PG 连接复用/超时生效/慢查询告警，已加入 CI `infra` job）；本机 702 收集 = 664 通过 + 38 跳过，ruff 全过 |
| 2026-09-27 | P2-4 SSE LISTEN/NOTIFY 同步：§3.2 增补 SSE 完成通知项勾选（同事务 `pg_notify` + 专连接 LISTEN 自动重连 + 轮询兜底 + shutdown 收口）；新增 `tests/test_notify.py`（纯逻辑 + 真实 PG 通知/回滚语义，已加入 CI `infra` job）；本机 706 收集 = 667 通过 + 39 跳过，ruff 全过 |
| 2026-09-27 | P2-6 告警外送同步：§3.7 告警触达项勾选（迁移 0013 `alert_states`/`alert_deliveries` + 指纹去重状态机 + webhook 退避重试 + CLI 三命令）；新增 `tests/test_alert_delivery.py`（状态机/退避/平台体格式/Worker 集成 + 真实 PG 契约，已加入 CI `infra` job）；本机 714 收集 = 674 通过 + 40 跳过，ruff 全过 |
| 2026-09-27 | P2-5a 审核 provider 抽象同步：§3.5 增补 provider 抽象项勾选（接口 + `local_rules` 默认实现 + 未知/异常回退 + provider 留痕 + egress 动态名，零外部 SDK）；新增 `tests/test_moderation_providers.py`（默认/未知回退/异常回退/自定义注入留痕/兼容旧签名 6 条）；本机 720 收集 = 680 通过 + 40 跳过，ruff 全过 |
| 2026-09-27 | P2-5b 申诉/复核状态机同步：§3.5 增补申诉状态机项勾选（迁移 0014 + 状态机 + accepted⇒cleared/rejected⇒flagged 语义 + SLA 告警 + CLI 三命令 + 180 天保留）；新增 `tests/test_appeals.py`（状态机/决策语义/SLA/API/告警 + 真实 PG 契约，已加入 CI `infra` job）；本机 727 收集 = 686 通过 + 41 跳过，ruff 全过 |
| 2026-09-27 | P2-7 容量标定同步：§3.7 增补容量标定项勾选（Locust 脚本 + 假图开关 `DR_LOADTEST_GRAPH` + `capacity-model.md` 公式/连接预算/基线表）；新增 `tests/test_loadtest_graph.py`（图工厂选择 3 条）；本机 730 收集 = 689 通过 + 41 跳过，ruff 全过 |
| 2026-09-27 | P2-8 发布/回滚 runbook 同步：新增 `docs/operations/release-runbook.md`（发布步骤 / 冒烟 / 观察窗口 / 回滚决策树 / 记录模板 / 演练状态如实登记）；§7.4 发布检查单引用 runbook 并明确「回滚演练未执行前不得视为已验证」；纯文档，测试基线 730 收集 = 689 通过 + 41 跳过不变 |
