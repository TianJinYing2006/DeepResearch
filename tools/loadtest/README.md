# 容量标定压测（P2-7，手动执行，不进 CI）

Locust 脚本：`locustfile.py`。目标是用**零外部依赖**的方式压出以下容量口径：

| 维度 | 被压路径 | 说明 |
|---|---|---|
| API 提交 | `POST /api/research`（配额/限流/幂等闸全开） | 准入吞吐、错误率、p95 |
| 队列与持久化 | API 入队 → Worker 领取 → `run_events` 落库 → `pg_notify` | 端到端时延、队列等待 |
| SSE 尾随 | `GET /api/research/{run_id}/stream` | 并发连接数、事件到达延迟 |
| 运维端点 | `/api/health/ready`、`/api/metrics` | 探针开销基线 |

## 1. 前置

```bash
pip install locust          # 刻意不进 requirements*.txt / lock（仅标定用）
```

在 **staging / 本地联调** 环境启动服务；压测 Worker 必须开假图（零 LLM 费用）：

```yaml
# worker 环境变量（仅压测时）
DR_LOADTEST_GRAPH: "1"        # 用 DemoGraph 替代真实研究图（不调 LLM / 不检索）
DR_LOADTEST_STEP_SECONDS: "0.05"
```

> ⚠️ `DR_LOADTEST_GRAPH=1` **生产环境不得设置**。压的是管道容量，不是研究质量。

鉴权开启时（staging 默认 `DR_AUTH_REQUIRED=true`）先创建一个专用压测账号，
压测账号的日限额建议调高，否则提交会被 409 配额拒绝：

```bash
python -m web.backend.admin create-user --email loadtest@example.com   # 打印一次临时密码
```

## 2. 运行

```bash
export DR_LOADTEST_EMAIL=loadtest@example.com
export DR_LOADTEST_PASSWORD='<上一步打印的密码>'
export DR_LOADTEST_TOPIC_PREFIX=压测

# 无 UI（CI/远程机）
locust -f tools/loadtest/locustfile.py --host http://127.0.0.1:8000 \
       --headless -u 20 -r 2 -t 5m --csv reports/loadtest

# 有 UI（本机观察）
locust -f tools/loadtest/locustfile.py --host http://127.0.0.1:8000
```

口径：

- 提交被配额 / 限流 / 幂等拒绝（409 / 429）按**预期拒绝**计入成功，
  拒绝占比在报告里通过 `POST /api/research` 请求数与不同返回码观测；
- 读流上限 `DR_LOADTEST_STREAM_SECONDS`（默认 5s），避免长连接拖住压测机；
- 建议三档递增：`-u 5` → `-u 20` → `-u 50`，每档观察 p95、5xx、队列深度、
  `pg_stat_activity` 连接数与 Worker CPU。

## 3. 清理

压测任务会进入任务库并按 P2-2 保留政策自动清理（事件 30 天 / 运行 90 天）；
如需立即清理：

```bash
python -m web.backend.admin retention-run --dry-run   # 先看候选量
```

活动中残余任务可由 Worker 租约接管后自然终局（假图秒级完成）。

## 4. 结果去哪

- 原始 CSV 放 `reports/loadtest/`（不入库）；
- 结论（实测吞吐 / 时延 / 连接数上界）回填
  `docs/operations/capacity-model.md` 的「实测基线」表。
