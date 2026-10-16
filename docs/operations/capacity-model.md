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

## 5. 实测基线（2026-10-16 回填）

| 部署 | 副本数 | 提交吞吐（req/s） | 提交 p95 | SSE 并发 | 队列等待 p95 | 连接峰值 | 结论 |
|---|---|---|---|---|---|---|---|
| 本地联调（Windows 进程内 API+Worker，同机 Locust，假图 0.05s/节点） | 1 API + 1 Worker | 5.0（-u 50 档；三档均 0 失败） | 47ms | ≈50（-u 50 档，5s 持有） | 170.8s（15min 全量积压所致） | 13 | 管道吞吐由**单 Worker** 决定（≈1.1 run/s）；API/SSE/探针路径未见瓶颈 |
| staging（待测） | 待定 | 待测 | 待测 | 待测 | 待测 | 待测 | 流程见 `tools/loadtest/README.md`（worker 开 `DR_LOADTEST_GRAPH=1` + 压测账号）；对外并发口径以 staging 复测为准 |

**本地联调三档明细（每档 5 分钟，全部 0 失败）**

| 档位 | POST 吞吐 | POST p95 | POST max | 聚合吞吐 | 队列表现 |
|---|---|---|---|---|---|
| -u 5 | 1.21 req/s | 41ms | 230ms | 2.29 req/s | 未积压 |
| -u 20 | 1.97 req/s | 42ms | 240ms | 3.66 req/s | 开始积压 |
| -u 50 | 5.02 req/s | 47ms | 220ms | 8.47 req/s | 活跃闸 200 封顶（队列峰值 199） |

**全量汇总（15 分钟）**：创建 1276 run / 完成 1132 / 队列等待 p95 **170.8s**（max 171.3s）/ 运行时长 p95 **0.9s**（假图）/ PG 连接峰值 13。

**口径声明（如实记录）**：

- 压测按标定目的放宽了闸门：提交限流 10→600/min、日限额 100000、用户/全局并发 200；**活跃闸（200）是本次唯一被触发的闸**，超限提交按 README 口径计为预期拒绝（不计失败）；
- worker 为 `DR_LOADTEST_GRAPH=1` 假图（零 LLM / 零检索）——压的是管道容量，不是研究质量；
- **同机压测**（Locust 与 API/Worker 共享本机资源），上表为**本机相对容量口径**；
- 容量结论：单 Worker 处理速率 ≈ **1.1 run/s**（0.9s/run p95），提交速率超过该值后队列等待线性增长（p95 170.8s 即 15 分钟内 3 档累计积压的体现），并在活跃闸处封顶；**对外宣称并发能力前必须先做 staging 行复测**（§4 纪律不变）。

## 6. 档位耗时标定（需求 35 R5 / TBD-7，2026-10-13）

**数据源**：staging 任务库 `runs`（终局且 `started_at` / `finished_at` 齐全，按
`request.profile.name` 分组；全量窗口）。统计工具 `tools/profile_latency.py`
（服务器 PG 未公网暴露，本次经**等价 SQL** 在容器内执行，口径与工具一致）。

| 档位 | 样本 n | p50 (s) | p90 (s) | avg (s) | max (s) | 备注 |
|---|---|---|---|---|---|---|
| quick | 10 | 26.1 | 35.7 | 26.6 | 61.5 | 样本不足（<20，如实标注） |
| standard | 1 | 302.2 | 302.2 | 302.2 | 302.2 | 样本不足（<20，如实标注） |
| deep | 0 | - | - | - | - | 无数据，不得推断 |

**结论（是否值得做并行实验，TBD-7 口径）**

- quick 档（n=10）p90 ≈ 35.7s：短链路交互体感已可接受，**并行收益有限**；
- standard 单样本 ≈ 302s（5 分钟级）：长链路主要成本在「子问题 × 逐跳研究」串行段，
  理论上子问题级并行（LangGraph `Send`）可重叠该段 —— **值得做固定题集对照实验验证**；
- deep 档无样本；**样本不足的档位不作为决策依据**（quick 补测不能替代 standard / deep）；
- **决策记录：暂不启动 `Send` 开发**。触发条件 = standard / deep 各积累 ≥20 个终局样本后，
  按下方方案执行对照实验，再决定是否引入 `Send`。

**固定题集对照实验方案（待样本达标后执行）**

| 项 | 设定 |
|---|---|
| 题集 | 10 题固定题集（3 领域 × 2 复杂度；题型对齐各档 DoD；冻结入库 `samples/`） |
| 对照 | A：现状（子问题串行）；B：`Send` 子问题并行（同一提交、同题集、同档位） |
| 指标 | 完成时间（p50 / p90）、费用（`usage_ledger` 对账）、质量（引用通过率 + 独立裁判；`SCORER_VERSION` 冻结） |
| 样本 | 每臂每档 ≥20 次（quick 仅作回归参照，不用于并行决策） |
| 预算 | 按档位预算上限预估：quick ¥0.75 / standard ¥1.25 / deep ¥1.50（单次） |
| 纪律 | 结果如实回填本表；结论只回答「是否值得引入 `Send`」，不夹带其他决策 |
