# 容量模型（P2-7）

> 口径：本文给出**管道容量**（API / 队列 / SSE / PostgreSQL）的估算公式与测量方法。
> 研究吞吐还受 LLM / 检索外部服务限制（单次真实运行 48~51 分钟、¥0.79~0.92 量级），
> 与本模型叠加时以外部服务配额为准。**实测基线表待按 §4 流程回填**——在回填之前，
> 不得对外宣称具体并发能力。

## 1. 组件与关键常量（默认值均可在环境变量覆盖）

| 组件 | 常量 | 默认 | 影响 |
|---|---|---|---|
| API | `DR_PG_POOL_MAX` | 8（每 API 进程） | API 侧 PG 连接上限 |
| Worker | `DR_PG_POOL_MAX` | 8（每 Worker 进程） | Worker 侧 PG 连接上限 |
| PG | `statement_timeout` | 15s | 单条 SQL 上界；超时=请求失败 |
| PG | `max_connections` | 100（compose 默认） | 全局连接预算（见 §3.2） |
| SSE | `DR_SSE_POLL_SECONDS` | 5s | 通知丢失时的最坏事件延迟 |
| SSE | `HEARTBEAT_SECONDS` | 15s | 空闲连接保活 |
| 队列 | `DR_MAX_CONCURRENT_RUNS` | 1（in-memory 闸） | 单进程并发研究数 |
| Worker | `DR_WORKER_LEASE_SECONDS` | 120s | 任务租约；崩溃接管时延上界 |
| Worker | `DR_WORKER_SWEEP_SECONDS` | 30s | 过期租约清扫周期 |
| Worker | `DR_ALERT_CHECK_SECONDS` | 60s | 告警同步开销周期 |
| 保留 | `DR_RETENTION_*_DAYS` | 30/90/180 | 存储增长上界（见 §3.3） |

## 2. 公式

- **准入吞吐**：`λ_submit ≤ min(限流锁, 配额闸, PG 写吞吐)`。
  每用户受 `DR_SUBMIT_RATE_PER_MINUTE`（默认 10/min）与
  `DR_DAILY_RUNS_PER_USER`（默认 1/天）双重限制；多用户压测前先放宽压测账号配额。
- **队列等待（Little's law）**：`W_queue ≈ L_queue / μ_worker`，
  其中 `μ_worker = 1 / E[运行时长]`（假图 `DR_LOADTEST_STEP_SECONDS × 11 节点`；
  真实运行取实测均值）。`L_queue` 由 `/api/metrics.queue_depth` 读取。
- **端到端事件延迟**：`T_event ≈ T_persist + T_notify`，
  其中 `T_persist` 为 `append_event` 事务耗时（受 `statement_timeout=15s` 硬上界），
  `T_notify` 正常 <50ms，通知丢失时退化为 `DR_SSE_POLL_SECONDS`。
- **SSE 连接**：每连接 1 个 API 线程（追随时阻塞在 executor 等待通知），
  `N_sse ≤ 线程池上限`；压测必须实测单实例可稳定维持的连接数再乘副本数。

## 3. 资源上界

### 3.1 连接预算（最重要）

```
所需 PG 连接 ≈ N_api × DR_PG_POOL_MAX
             + N_worker × DR_PG_POOL_MAX
             + N_listener（每 API 进程 1 条 LISTEN 专连接）
             + 管理/迁移/CLI 余量（建议 ≥ 10）
必须 ≤ max_connections（compose 默认 100）
```

例：2 API × 8 + 2 Worker × 8 + 2 + 10 = 44 —— 单机 compose 裕量充足；
扩到 5 API × 5 Worker 时需同步调高 `max_connections` 或收紧池上限。

### 3.2 存储增长（每 run）

- `run_events`：约 `节点数 + 2` 行（默认图 11 节点）；payload 含进度/降级元数据，
  单行通常 < 2KB；
- `runs` 1 行、`run_artifacts` 1~2 行（报告正文默认落对象存储，PG 只留元数据）；
- `usage_ledger`：每模型调用 1 行；
- 上界由保留政策截断：事件 30 天、运行 90 天、账本/审核/审计 180 天。
  按 1000 run/天估算，稳态行数 ≈ 事件 3 万行/天 × 30 天 ≈ 90 万行量级，需监控表体积
  与 autovacuum（`retention_purge` 审计可对账删除量）。

### 3.3 外部成本

假图压测零成本；真实压测按 `usage_ledger` 对账（CLI `usage-summary`），
并建议给压测账号设 `DR_MONTHLY_BUDGET_CNY` 类型的月度上限（运行参数预算亦可）。

## 4. 测量流程（对应 `tools/loadtest/README.md`）

1. worker 开 `DR_LOADTEST_GRAPH=1`（零 LLM），API 保持全闸（鉴权/限流/配额）；
2. 压测账号放宽日限额；依次 `-u 5 / 20 / 50`，每档 5 分钟；
3. 每档记录：`POST /api/research` p95、5xx 比例、`/api/metrics.queue_depth`、
   `pg_stat_activity` 连接数峰值、SSE 并发连接数、Worker CPU；
4. 出现 5xx / `statement_timeout` / 连接池等待超时（`PoolTimeout`）即视为
   该档已到边界，取**上一档稳定值**为容量上限；
5. 结论回填下表，并把 §7.5 告警阈值随之校准（生产就绪清单要求）。

## 5. 实测基线（待回填）

| 部署 | 副本数 | 提交吞吐（req/s） | 提交 p95 | SSE 并发 | 队列等待 p95 | 连接峰值 | 结论 |
|---|---|---|---|---|---|---|---|
| 本地 compose（待测） | 1 API + 1 Worker | 待测 | 待测 | 待测 | 待测 | 待测 | 待测 |
| staging（待测） | 待定 | 待测 | 待测 | 待测 | 待测 | 待测 | 待测 |

> 本仓库当前**未在目标硬件上完成实测**；上表为空属如实记录（P2-7 交付脚本、公式与流程，
> 数值需按 §4 执行后回填）。在此之前，扩容决策按 §3.1 连接预算与外部服务配额做保守估计。
