# ADR-0009：L3 多用户生产化架构（状态外置 / Worker / 用户体系 / 部署底座）

## 基本信息

- **编号**：0009
- **标题**：L3 多用户生产化架构：PostgreSQL 权威状态 + Redis 队列/租约 + 独立 Worker + 用户/配额 + 部署底座
- **日期**：2026-09-24
- **状态**：**草稿（P0 立项）** —— 首发参数决策表（`docs/requirements/10-l3-production.md` §3.1）拍板后转「已采纳」；拍板前不进入实现
- **涉及模块**：`web/backend/`（`runner.py` / `main.py` 的职责外置与加固）、新增 `worker/`、新增部署文件（`Dockerfile.api` / `Dockerfile.worker` / `docker-compose.staging.yml` / 反向代理配置 / 数据库迁移）、`docs/operations/`、`docs/requirements/10-l3-production.md`；`research_engine/` **只读复用，不改判定口径**
- **关联**：需求 10（本 ADR 的需求侧落点）；D-19 / D-20 / ADR-0001 §1.1（本 ADR 显式解除其 L3 边界）；`docs/operations/production-readiness.md`（上线准入清单）

## 背景

截至 2026-09-24，项目的事实基线（`docs/project-status.md`）是：核心研究链路、单用户 Web MVP、测试与 CI 完成度高 —— pytest **455 全绿**、浏览器 E2E **10/10**、ruff + Python 3.11/3.12/3.13 matrix + `frontend` / `e2e` job 全绿。

但运行底座仍是**单进程内存态**：

```text
浏览器 → FastAPI → 进程内 RunManager → Python 工作线程 → LangGraph 同步生成器 → 搜索 / LLM / Qdrant
```

证据（读码所得）：

| 事实 | 代码位置 |
|------|---------|
| 运行状态、事件帧、结果、报告、元数据全部在进程内存 | `web/backend/runner.py:114-130`（`_frames` / `_results` / `_reports` / `_meta` / `_active`） |
| 状态查询接口自述「内存态，重启即失（D-19 不做持久化）」 | `web/backend/main.py:166-175` |
| 并发闸是进程内集合，多实例不共享 | `runner.py:59`（`DEFAULT_MAX_CONCURRENT_RUNS = 1`）、`runner.py:107`（`DR_MAX_CONCURRENT_RUNS`） |
| 无鉴权：任何持有 `run_id` 者可查询 / 取消 / 导出报告 | `main.py` 全部路由无用户上下文；`runner.exists()` 只认 `run_id` |
| CORS 固定 localhost 开发源 | `main.py:47-52` |
| RAG 全局单 collection、payload 无租户字段、检索无 filter | `config.py:89`（`collection="deepresearch_docs"`）、`research_engine/rag/ingest.py:108-113`、`research_engine/rag/retriever.py:83` |
| 成本只有运行级上界估算，无用户级计量 | `runner.py:74`（`_estimate_cost_cny`，按最贵 output 单价计的保守上界） |

而三份既有决策把 L3 所需的产品化能力明确挡在门外：

- **ADR-0001**：不做完整产品（Web UI / 用户系统 / 任务队列等外围功能），聚焦核心链路；
- **ADR-0001 §1.1 修订**：允许升级呈现层技术栈，但**不扩展产品边界**；
- **D-19**：任务模型定为「前台跑 + 可取消」，明确接受「关掉页面任务即丢失」；
- **D-20 硬边界②**：不引入登录、用户系统、任务队列、持久化任务、多租户、权限、云端部署。

因此 L3 不能靠「在 D-20 里继续加功能」实现，必须**新增决策显式解除部分边界并重建任务底座** —— 这就是本 ADR。历史决策原文一律不改（只追加，见「理由与取舍」）。

## 问题 / 动机

L3（对外可用）的阻塞按优先级排序：

1. **任务不能持久化**（第一优先级）。API 进程重启、发布、崩溃即丢全部运行状态与在跑任务；多实例之间无法共享状态 ⇒ 不能可靠支持历史报告、跨设备查看和故障排查。
2. **48~51 分钟默认耗时**（实测，`docs/eval-w8-after-baseline.md`）对内部工具可接受，对公开产品是体验风险 ⇒ 必须提供 Quick / Standard / Deep 分档，且档位参数由**服务端策略**封顶，而不是前端自由填写。
3. **无用户级配额与成本闸**。当前只有运行级上界估算（¥0.79~0.92/轮），没有每日上限、单用户上限、全局熔断 ⇒ 一次滥用即可烧穿预算。
4. **无内容安全链路**。输入预检、输出审核、投诉、封禁、审计、日志留存全部缺失，正式开放前无法满足《生成式人工智能服务管理暂行办法》相关义务。
5. **数据流向未定案**。现状同时使用阿里云百炼（DashScope LLM/Embedding）、博查、Tavily、Langfuse Cloud、Qdrant —— 研究问题、报告、用户文件的流向与留存未逐项确认；境外服务（Langfuse Cloud / Tavily）可能比代码更早成为上线阻塞。
6. **无正式部署工程**。无 Dockerfile、staging、迁移、反向代理、备份、恢复演练、回滚、密钥管理；CORS 仍固定 localhost。

补充语义问题：当前「是否取消」与「研究成功」混在传输层与 `run_status` 之间，一旦持久化，`CANCELLED` 的 run 可能仍带 `stop_reason=completed` 一类旧口径会变得难以解释 ⇒ 需要在持久化时一并重建状态机。

## 方案

### 1. 目标架构

```text
                 ┌────────────────────┐
                 │  CDN / WAF / HTTPS │
                 └─────────┬──────────┘
                           │
                 ┌─────────▼──────────┐
                 │ Nginx / API Gateway │
                 └─────────┬──────────┘
                           │
              ┌────────────▼────────────┐
              │       FastAPI API        │
              │ 鉴权 / 配额 / 审核 / SSE │
              └───────┬────────┬────────┘
                      │        │
            ┌─────────▼──┐ ┌──▼─────────┐
            │ PostgreSQL │ │   Redis    │
            │ 权威状态    │ │ 队列/限流   │
            │ 事件/配额   │ │ 实时事件    │
            └────────────┘ └────┬───────┘
                                  │
                         ┌────────▼────────┐
                         │ Research Worker │
                         │ 独立进程运行图   │
                         └──────┬─────┬─────┘
                                │     │
                      ┌─────────▼┐ ┌──▼──────────┐
                      │ Qdrant   │ │ LLM/Search  │
                      │ 知识库    │ │ Providers   │
                      └──────────┘ └─────────────┘

                         ┌────────────────┐
                         │ Object Storage │
                         │ 报告/导出文件   │
                         └────────────────┘
```

### 2. 关键原则

**原则 1：PostgreSQL 是唯一事实来源。** 首版至少需要：`users` / `sessions` / `runs` / `run_events` / `run_checkpoints` / `run_artifacts` / `user_quotas` / `usage_ledger` / `moderation_records` / `audit_logs`。

**原则 2：每个 run 必须带用户与租户上下文。** `run_id` / `user_id` / `tenant_id` / `status` / `created_at` / `started_at` / `finished_at` / `cancel_requested_at` / `stop_reason` / `current_node` / `token_used` / `cost_estimate`。

**原则 3：每个事件必须有单调递增序号。** `run_events(run_id, sequence, event_id, event_type, payload, created_at)`；SSE 连接按下列流程：

```text
1. 客户端带 Last-Event-ID 连接
2. API 先从 PostgreSQL 补发缺失事件
3. 再订阅 Redis 实时事件
4. 事件统一按 sequence 输出
5. 断线后可继续恢复
```

**原则 4：任务状态重新建模。** 状态机：`CREATED → QUEUED → RUNNING →（CANCEL_REQUESTED）→ SUCCEEDED / FAILED / CANCELLED / TIMED_OUT / LOST`；并独立保存 `research_status` / `stop_reason` / `worker_status`，不把「研究是否成功」与「任务是否取消」塞进同一个字段。

**原则 5：Redis 只做加速层，不做唯一事实来源。** Redis 负责队列、租约、限流、实时事件加速；允许用 Stream，但历史回放、审计、对账一律以 PostgreSQL 为准（理由见「理由与取舍」）。

**原则 6：任务目标分两层，首发只承诺第一层。**
- 第一层「任务不丢」：关页面不影响任务；API 重启后任务可查询；worker 挂掉后任务进入失败或可重试状态；用户能看到历史报告。
- 第二层「任务可恢复」（断点续跑）：从最后成功节点继续、不重复执行已完成节点、不重复扣费、人工/自动重试。首发**不做**第二层。

**原则 7：RAG 按租户隔离。** 首版单 collection + payload `tenant_id` / `user_id` / `visibility`，每次检索强制租户 filter；默认只开放 `private`（仅本人），不做共享空间。

**原则 8：最小用户体系与安全选型。** httpOnly Session Cookie + Argon2id 密码哈希 + 严格 CORS + CSRF 防护 + 登录/提交限流；所有查询强制带 `user_id` 条件；首版不用前端长期 JWT。

### 3. 分阶段与边界解除

- **L3-A 内部可用版** → **L3-B 邀请制内测版** → 合规确认与备案 → **L3-C 正式公开版**。各阶段范围、参数与 DoD 见 `docs/requirements/10-l3-production.md`。
- **本 ADR 解除的边界**（仅限 L3 范围）：
  - D-19 的「前台跑、任务随页面丢失」→ 由「任务持久化 + 后台 Worker」替代；
  - D-20 硬边界②的「不做登录 / 用户系统 / 任务队列 / 持久化任务 / 多租户 / 云端部署」→ 在 L3 范围内解除；
  - ADR-0001 §1.1 的「不扩展产品边界」→ L3 是产品化立项，边界由需求 10 重新圈定。
- **本 ADR 明确保留的纪律**：
  - `research_engine/` 判定口径不动（W8 冻结纪律、A 类验收 100% 可归因率不受影响）；
  - `research_engine/eval/results/` 历史产物继续冻结；
  - CLI 仍是第一公民；Web/Worker 只消费 `iter_run()`，不复制判定逻辑；
  - 单用户 MVP 的运行语义（取消契约 C1~C7、超时/强制收口口径）原样迁移到 Worker。

### 4. 首发不做清单（防止范围膨胀）

多区域容灾、复杂组织架构、企业级 RBAC、真实支付与退款、多模型自由编排、用户自定义 Agent、插件市场、实时协作编辑、全量历史搜索、Kubernetes、多云部署。

### 5. 从当前实现迁移的映射（RunManager → 持久化模型）

| 现内存字段（`web/backend/runner.py`） | 现语义 | L3 落点 |
|------|------|---------|
| `_frames[run_id]`（下标即事件序号） | SSE 帧缓冲 / 重连回放 | `run_events.sequence` + Redis 实时加速 |
| `_results` / `_reports` / `_meta` | 终局结果 / 报告正文 / 元数据 | `runs` 终局列 + `run_artifacts`（大正文进对象存储） |
| `_status[run_id]` | 运行画像快照 | `runs` 列（状态、耗时、token、成本、`current_node`、`stop_reason`、`worker_status`） |
| `_cancel[run_id]`（`threading.Event`） | 进程内取消信号 | `runs.cancel_requested_at` + Redis 信号 |
| `_active`（set） | 进程内并发闸 | `runs.status` 计数 + Redis 并发信号量 |
| `_deadlines` / `_hard_deadlines` | 协作式 / 硬截止时刻（monotonic） | `runs.timeout_at` / `hard_deadline_at`（UTC 时间戳） |
| `_worker` 线程 + `queue.Queue` | 请求进程内执行 graph | 独立 Worker 进程消费 Redis 队列 |
| `_emit*` / `_append_locked`（同锁原子终局） | 终局原子化 | DB 事务 + 事件表唯一 `sequence` 约束（保持 ADR-0008 语义） |

迁移纪律：

1. **新增不改旧**：`RunManager` 保留给本地演示与既有测试（`DR_DEMO=1` 路径不受影响），L3 栈是增量，不做「先删内存态再加持久化」的中间态。
2. **事件序号语义不变**：当前帧下标从 0 递增，`Last-Event-ID` 即下标；持久化后 `sequence` 起点必须一次性定死并写进契约测试。
3. **终局纪律不变**：终局帧 / 结果 / 状态必须一次原子写入（ADR-0008）；迁移到数据库后由「同一把锁」升级为「同一事务」，不得退化为多次提交。
4. **取消 / 超时 / 预算不写 `run_status`**：继续沿用 W8 三态封闭与故障可归因率 100% 的口径。
5. **Provider 抽象保留**：境外服务默认关闭只影响默认配置与部署开关，不删除 `tavily` / `langfuse` / `arxiv` 代码路径与测试 mock。

## 理由与取舍

- **为什么用 PostgreSQL 做事实来源，而不是只上 Redis Pub/Sub**：Pub/Sub 不保留历史 —— 客户端断线期间的消息丢失、API 重启后事件无法回放、`Last-Event-ID` 无法可靠对应历史事件、审计与故障排查没有长期记录。Redis 可以加速，但不能作为唯一事实来源。
- **为什么首发只做「任务不丢」而不是完整断点续跑**：真正的断点恢复需要同时满足 —— 每个节点完成后保存 checkpoint、可序列化的 graph state、节点副作用幂等、外部搜索/LLM 调用有记录、worker 崩溃后能识别最后完成节点、重试不重复扣费、任务租约与超时恢复。七项一次做齐会拖垮首发；先做到「任务不丢」即覆盖绝大多数用户可感知损失。**不要一上来承诺完整断点续跑。**
- **为什么保留同步 `graph.stream()`**：Worker 进程直接执行同步图即可。为了「看起来现代」把整套图改成异步，收益低且会触碰 W8 冻结的判定口径，风险收益不成比例。
- **为什么用成熟队列方案而不是自研调度器**：租约、可见性超时、重试与死信是队列的成熟能力，自研调度器是可靠的 bug 温床。第一版选用一个成熟、简单的 Python 队列方案（P3 拍板具体选型）。
- **为什么单 collection + payload filter，而不是每用户一个 collection**：少量用户场景下单 collection 更易维护，跨用户共享公共知识也只需放宽 filter；per-collection 会带来 collection 爆炸与迁移负担。
- **为什么 httpOnly Session Cookie 而不是前端长期 JWT**：Web 单页应用 + 同源部署下 Session Cookie 更简单、可服务端即时吊销；长期 JWT 在 XSS 下不可挽回，且首版没有多端离线鉴权需求。
- **为什么不推翻历史决策**：ADR / D 编号是**项目记忆**，只追加、不删改。D-19 / D-20 / ADR-0001 §1.1 的原文保留；「L3 范围内这些边界已解除」的事实由本 ADR 声明并指向，避免出现「已解除的旧约束被当成仍有效」的漂移。
- **代价**：新增 PostgreSQL / Redis / 对象存储的运维面；新增迁移、备份、恢复演练、租户隔离与鉴权测试；单进程 MVP 的「简单」消失。这些代价正是多用户上线的门票，无法绕过。

**备选方案（已否决）**：Redis Pub/Sub 作为事件唯一来源；首发即做完整 checkpoint 断点续跑；把 graph 整体改异步；自研数据库轮询式任务调度；前端长期 JWT；每用户一个 Qdrant collection；首发即上 Kubernetes / 多地域。

## 影响

- **新增**：`worker/`（独立进程）、`docs/requirements/10-l3-production.md`、`docs/operations/production-readiness.md`、部署文件（`Dockerfile.api` / `Dockerfile.worker` / `docker-compose.staging.yml` / 反向代理配置）、数据库迁移与备份/恢复脚本。
- **修改**：`web/backend/main.py`（鉴权、CORS 环境变量化、配额/审核接入）、`web/backend/runner.py`（内存态职责迁移到 PG/Redis；本地演示与测试仍可复用）；`web/frontend/` 增加登录、任务列表/历史、配额展示；CI 增加迁移与 worker 测试 job。
- **不改**：`research_engine/` 判定口径与图结构；CLI；`research_engine/eval/results/` 冻结产物；既有 455 条 pytest 基线必须保持全绿。
- **对项目定位的影响**：仓库性质从「纯 Python 研究项目」扩展为「全栈产品」，但核心卖点与验收口径不变（核心 Agent 编排 + 可量化结果）。
- **未拍板前的影响**：零实现改动。本 ADR 与需求 10、上线准入清单仅作为决策与计划载体存在。

## 变更记录

| 日期 | 变更说明 |
|------|---------|
| 2026-09-24 | 初始记录：L3 生产化架构立项草稿；显式解除 D-19 / D-20 / ADR-0001 §1.1 的 L3 边界；确立 PG 权威状态 + Redis 队列 + 独立 Worker + 任务不丢优先的策略；首发参数决策表见需求 10 §3.1（待拍板） |
| 2026-09-24 | 追加 §5 迁移映射：`RunManager` 内存字段 → PostgreSQL / Redis 落点；明确本地演示路径保留（`DR_DEMO` 不受影响）、事件序号与终局原子化纪律不变；同步需求 10 §5.9~§5.13 的任务模型细化、API 面、审计计量与配置清单 |
