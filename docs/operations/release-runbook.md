# 发布与回滚 Runbook（P2-8）

> 配套：生产就绪清单 §7.4（发布检查单）、§7.3（备份恢复命令基线）、
> `docs/operations/capacity-model.md`（连接预算）、`tools/backup.sh` / `tools/restore.sh` /
> `tools/migrate.sh`。CI 每次推送都跑「备份→清库→恢复」演练（`infra` job），
> 但**应用版本回滚演练尚未在 staging 实际执行**（见 §9 如实登记）。

## 0. 原则

1. **迁移前向兼容、不写回滚脚本**：迁移只做增量（加表 / 加列 / 加索引），
   旧版本应用进程在迁移后仍可运行（新表被忽略）；灾难恢复走备份，不走 `DROP`。
2. **先迁移，后切流**：`migrate` 完成且结构断言通过后，才启动新版本 API / Worker。
3. **回滚 = 应用版本回滚**：数据库保持在新结构；只有当新结构破坏旧代码时才需要
   数据级回退（按 §5，需停写 + 备份恢复）。
4. 发布与回滚都要留下记录（版本、时间、操作人、观察结论）。

## 1. 角色与前置

| 角色 | 职责 |
|---|---|
| 发布执行人 | 按本 runbook 操作、填写记录 |
| 观察人 | 观察窗口内盯指标，有权触发回滚 |
| 决策人 | 回滚决策（§6 决策树），对外沟通 |

前置：目标环境 `DR_ENV=staging|production` 配置齐全（启动硬校验会 fail fast）；
发布镜像按 **git SHA tag** 构建（禁止 `latest` 滚动覆盖）。

## 2. 发布前（T-0，发布窗口内）

```bash
# 2.1 变更冻结确认：CI 全绿（dev 分支 6/6 job）
gh pr checks <PR号>            # 发布提交对应 PR

# 2.2 迁移评审（人工过一遍本次新增迁移文件与 checks/*.sql）
ls migrations/00*.sql | tail -3

# 2.3 备份 + 校验可读（P8-A 脚本；失败即中止发布）
bash tools/backup.sh            # pg_dump -Fc + pg_restore -l 可读校验 + 轮转保留
# 可选加密（需求 20 §8）：BACKUP_ENCRYPT=1 BACKUP_PASSPHRASE=... bash tools/backup.sh
#   → 产出 deepresearch_*.dump.enc（AES-256-CBC + PBKDF2 200k，校验后删明文）；
#     口令仅环境变量注入、异地托管（同机存放 = 没加密）；恢复见 .env.example 注释同口径

# 2.4 配置 diff 核对：CORS / 预算 / 并发 / 限流 / 境外服务开关 / webhook
docker compose -f docker-compose.staging.yml config | grep -E "DR_(CORS|MONTHLY|MAX_CONCURRENT|ALERT|S3|ENV)"

# 2.4b 对象存储兼容核对（腾讯云 COS，2026-10-01 现场验证）：
#   - DR_S3_ADDRESSING_STYLE 必须为 virtual（auto 会选 path-style 被 PathStyleDomainForbidden 拒绝）
#   - 桶级 PutBucketLifecycleConfiguration 依赖 objectstore 的 Content-MD5 注入钩子
#     （见 web/backend/objectstore.py 模块 docstring 兼容表；缺失会报 InvalidRequest: Missing required header）
#   - MinIO/OSS 场景按 docstring 兼容表核对，不要照抄 COS 结论

# 2.5 容量检查：连接预算（capacity-model §3.1）
# N_api×DR_PG_POOL_MAX + N_worker×DR_PG_POOL_MAX + LISTEN + 余量 <= PG max_connections
```

## 3. 发布步骤（T-0）

```bash
# 3.1 拉取发布提交并构建镜像（tag = git SHA）
git fetch origin && git checkout <release-sha>
export DR_IMAGE_TAG=$(git rev-parse --short HEAD)
docker compose -f docker-compose.staging.yml build

# 3.2 迁移（单事务批量执行 + 结构断言；失败即停止）
docker compose -f docker-compose.staging.yml up -d --wait postgres redis minio
docker compose -f docker-compose.staging.yml run --rm migrate
docker compose -f docker-compose.staging.yml run --rm migrate   # 二次执行应为幂等空跑

# 3.3 先起 Worker，再切 API（队列模式下新 Worker 可消费旧任务）
docker compose -f docker-compose.staging.yml up -d --wait worker
docker compose -f docker-compose.staging.yml up -d --wait api
```

## 4. 冒烟（新版本 5 分钟内完成）

```bash
BASE=https://<staging-host>
curl -fsS $BASE/api/health/ready | jq '.ok, .checks'          # readiness 全绿

# 登录 → 提交 → 尾随 → 取消 → 导出（P4-A 鉴权开启时带 Cookie + CSRF）
# 用管理员 CLI 侧核对（示例）：
python -m web.backend.admin audit-list --limit 5
python -m web.backend.admin alerts-list --status firing
python -m web.backend.admin alert-deliveries --undelivered --limit 5
python -m web.backend.admin appeal-list --status pending
```

浏览器冒烟（真实环境清单 §7.1）：提交一次真实运行 → 看进度帧与心跳 →
取消一次 → 导出 md/json；确认 15s 心跳不断流。

## 5. 观察窗口（15~30 分钟）

| 信号 | 来源 | 阈值/动作 |
|---|---|---|
| 5xx 比例 | `/api/metrics` 或 Prometheus | >2% 持续 5min ⇒ 评估回滚 |
| 队列深度 / 等待 | `/api/metrics.queue_depth`、告警 `queue_depth` | 持续增长且 Worker 心跳正常 ⇒ 查 Worker 日志 |
| Worker 心跳 | `/api/metrics.workers_live` | 0 ⇒ 立即排查/回滚 |
| 慢查询 | Worker/API stderr `deepresearch.pg` WARNING | 突增 ⇒ 检查新迁移/查询 |
| 连接数 | `pg_stat_activity` | 接近 `max_connections` ⇒ 调池或扩容 |
| 告警外送 | `alert_deliveries`（`--undelivered`） | 堆积 ⇒ 查 webhook |
| 备份任务 | 每日备份日志 | 失败即告警（P2-6） |

## 6. 回滚决策树

```
新版本异常？
├─ readiness 失败 / API 起不来
│    → 回滚 API/Worker 镜像到上一个 tag（§6.1），DB 不动；修复后重新走 §3.2 起
├─ 错误率/队列持续恶化，重启无效
│    → 回滚应用镜像；保留新表（旧代码忽略之），把异常 run_id/request_id 交复盘
├─ 迁移执行中失败（migrate job 退出非 0）
│    → 单事务已整体回滚，DB 处于迁移前状态；回滚应用镜像即回到上一版本全量
└─ 数据损坏 / 迁移不可逆灾难（极端）
     → 进入停机维护：停 API/Worker → tools/restore.sh 恢复最近备份（§5 演练同一路径）
       → readiness + 冒烟 → 恢复服务；期间损失窗口内数据，必须公告
```

### 6.1 应用回滚命令

```bash
export DR_IMAGE_TAG=<上一个绿版本 SHA>
docker compose -f docker-compose.staging.yml build
docker compose -f docker-compose.staging.yml up -d --wait worker api
curl -fsS $BASE/api/health/ready
python -m web.backend.admin audit-list --action release_rollback --limit 5  # 记录
```

> 新表（如 `alert_states` / `moderation_appeals`）在旧版本被忽略，无需清理；
> 待下次发布继续使用。保留期清理（P2-2）会按政策自动收敛这些表。

## 7. 特殊场景

- **只改前端**：仍走同一流程（镜像内 `frontend/dist` 由 CI 构建产物进入镜像）；
- **只改配置**：无需迁移；`docker compose up -d` 滚动即可，冒烟后观察 15min；
- **紧急热修**：允许从 `main` 对应 hotfix 分支发布，但必须补 PR 回合并到 dev；
  跳过 §2.2 仅限不涉及迁移的改动，且需双人复核。

## 8. 记录模板（发布后 10 分钟内）

```text
发布日期/时间：
版本（git SHA）：            上一版本：
迁移：0001~00NN（本次新增：____）
备份点：<路径/时间>           校验：通过/失败
配置 diff：无变化 / <摘要>
冒烟：通过 / 失败（<现象>）
观察窗口：__ 分钟；5xx=__%；队列 p95=__；workers_live=__
告警外送：已收（渠道：__）/ 未配置（记录） / 堆积（处理：__）
结论：保持 / 回滚（原因）
```

## 9. 演练状态（如实登记）

| 演练 | 状态 | 说明 |
|---|---|---|
| 备份 → 清库 → 恢复 | ✅ 每次 CI `infra` job 执行 | `tools/backup.sh` + `tools/restore.sh` + 种子数据/结构断言双向验证 |
| 应用版本回滚 | ⏳ 未演练（staging 执行后回填本节） | 本文 §6.1 为流程；回滚能力验证前，发布窗口需预留人工介入 |
| 迁移失败中断 | ⏳ 未演练 | 依赖 migrate 单事务语义；可在 staging 用损坏 fixture 演练 |
