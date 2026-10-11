# 部署形态与运维（staging 实测）

> **定位**：本文件是**部署与运维的唯一真源**（服务清单、端口、部署流程、已知缺口）。
> 上线准入事实以 `production-readiness.md` 为准，发布/回滚流程以 `release-runbook.md` 为准，
> 实时项目状态以 `docs/project-status.md` 为准。
>
> ⚠️ **本文件会过期**：任何服务增减、端口变更、profile 调整都必须同步更新 **§7 变更记录**。
> 历史教训：本项目的文档漂移已造成过误判（`launch-readiness-review.md` 的 09-30 快照被当作当前事实引用）。
>
> **安全口径**：本文件进入公开仓库，**不得写入真实主机 IP、域名、口令与密钥**，一律用占位符。
>
> 最后核对：**2026-10-07**（与 `docker-compose.staging.yml`、`Caddyfile` 逐项核对过）。

---

## 1. 总体形态

| 项 | 值 |
|---|---|
| 部署方式 | **文件同步式部署**（服务器目录**非 git 仓库**） |
| 编排 | 单机 `docker compose -f docker-compose.staging.yml` |
| 对外入口 | Caddy 反代（profile `proxy`）；API 容器**只绑定 `127.0.0.1`** |
| 执行模式 | `DR_EXECUTION_MODE=queue`（API 登记 `QUEUED`，Worker 租约 + 心跳消费） |
| 自动部署 | **无**。CI 只做质量门禁；合并 dev 后由主理人手动执行部署脚本 |
| 前端 | 在镜像内构建（`Dockerfile.api` 多阶段），服务器无需 node |

⚠️ **由此产生的可审计缺口**：服务器上没有 git ⇒ **无法回答"当前跑的是哪个 commit"**。
smoke 校验了 bundle hash（只有前端），Python 侧无版本锚点。见 §6 第 2 项。

## 2. 服务清单

| 服务 | 镜像 / 入口 | 作用 | 暴露面 |
|---|---|---|---|
| `api` | `Dockerfile.api`（node 构 dist → `python:3.13-slim` 装 lock；uvicorn :8000；**非 root uid 10001**） | FastAPI + 托管 dist | `127.0.0.1:8000` |
| `worker` | 同镜像，`python -m web.backend.worker` | 队列消费 run + 知识库摄取 | 内网 |
| `postgres` | `postgres:16-alpine` | 权威数据（runs / 审计 / 三层 KB） | `127.0.0.1:5432` |
| `redis` | `redis:7-alpine`（`maxmemory 200mb` + `allkeys-lru`） | 队列信号 / 限流键 | `127.0.0.1:6379` |
| `qdrant` | `qdrant/qdrant:latest` | 向量库（集合 `deepresearch_docs`） | `127.0.0.1:6333` |
| `migrate` | `postgres:16-alpine` 一次性容器 | 前向迁移 + `schema_migrations` 记账 | 一次性 |
| `caddy` | `caddy:2-alpine`（profile `proxy`） | 反代 / HTTPS | 公网 80 / 443 |
| 可选 profile | `local-s3`（MinIO）、`local-mail`（MailHog）、`errors`（GlitchTip） | 对象存储 / 邮件 / 错误追踪 | 默认不启动 |

**依赖门禁**：`api` 与 `worker` 都 `depends_on: migrate: condition: service_completed_successfully`
⇒ 迁移失败时应用**不会**带着旧 schema 起来。这个设计是对的。

**内存硬上限**（4 GB 轻量机）：postgres 900M / worker 700M / qdrant 640M / api 450M / redis 256M。
目的写在 compose 文件头：让失控服务只杀自己，而不是让内核 OOM killer 挑中 PostgreSQL（唯一有状态服务）。

## 3. 关键机制

- **配置透传**：服务器 `.env` + compose 的 `x-app-environment` YAML 锚点。
  ⚠️ 历史 bug：compose 未透传时 `.env` 调参对容器无效（PR #120 已修）。**新增任何 `DR_*` 配置项都必须同时加进锚点**，否则又是同一个坑。
- **启动硬校验（fail-fast）**：staging 缺失 `DR_CORS_ORIGINS` / `DASHSCOPE_API_KEY` / `DR_COOKIE_SECURE=true` / `DR_AUTH_REQUIRED=true` 即启动失败。
- **数据**：全部 Docker named volumes（`pg_data` / `qdrant_data` / `redis_data` / `rag_quarantine` / `caddy_data`）。
- **安全**：API 不直暴公网、强制 HTTPS、Secure Cookie、CSRF 双提交、Redis 滑动窗口限流、审计日志、分享与错误追踪默认关。
- **SSE 穿透**：`Caddyfile` 对 `/api/research/*/stream` 关闭压缩 + `flush_interval -1` + 读写超时 3900s（对齐 `DR_RUN_TIMEOUT_SECONDS=3600` 留 5 分钟余量）。上传在代理层限 12 MB。

## 4. 部署流程（手动，6 步）

1. 本地打 `tar.gz`：代码 + `migrations/` + `Dockerfile.api` + compose + `docs/legal` + `docs/help`（排除 `node_modules` / `dist`）
2. `scp` 到服务器 `/tmp`
3. 服务器脚本（**后台执行防断连**）：备份 → 解包覆盖 → `docker compose build api worker`
4. `docker compose run --rm migrate`
5. `docker compose up -d api worker`
6. smoke：`/api/options`、help/legal 端点、分享 404、bundle hash

> ⚠️ 第 3 步的「备份」是**部署包备份**（`/opt/deepresearch/backups/*.tar.gz`），用于**代码回滚**。
> 它**不是数据库备份**，不能恢复用户数据。见 §6 第 1 项。

## 5. 回滚与运维

| 项 | 现状 |
|---|---|
| 回滚 | 旧镜像按 tag 保留（如 `c118d70-adminfix`），可指定 tag 回退 |
| 部署包回滚 | 每次部署前备份存 `/opt/deepresearch/backups/*.tar.gz` |
| 探针 | `/api/health/ready` |
| 监控 | `/api/metrics`（`DR_OPS_TOKEN` 鉴权）、`/api/ops/alerts`（阈值在 `web/backend/alerts.py`） |
| 告警触达 | webhook（未配置时只维护 `alert_states`，零外呼） |
| 尚未启用 | SMTP（找回邮件）、GlitchTip（错误追踪）、release-runbook 的正式发布流程与 master 分支 |

## 6. 已知缺口与风险（按优先级）

### 6.1 P0 · 数据库备份未配置

服务器上有**代码包备份**（`.tar.gz`），但**没有 `pg_dump` 数据备份**。
两者不是一回事：代码坏了可以回退镜像，**数据库坏了（误删 volume / 迁移事故 / 主机故障）用户数据就是真丢**——
runs、账号、审计、知识库三层全在 `pg_data` 卷里，且当前已有真实内测数据。

最小可用做法（宿主机 cron，无需改 compose）：

```sh
# 每天 03:00；判据是 pg_restore -l 能列出目录，只落文件不算备份
0 3 * * * docker compose -f /opt/deepresearch/docker-compose.staging.yml exec -T postgres \
  pg_dump -Fc -U deepresearch deepresearch > /opt/deepresearch/backups/pg_$(date +\%Y\%m\%d_\%H\%M\%S).dump
```

校验：`pg_restore -l <dump>` 能列出即成功（与 `tools/backup.sh` 同口径；该脚本默认输出 `/backups`，保留 14 份，支持 `BACKUP_RECIPIENT_CERT` 非对称加密）。
配完后**必须做一次真实恢复演练**——CI 里的恢复演练跑的是 CI 环境，不代表云上可恢复。

### 6.2 P1 · 无构建版本锚点（非 git 仓库部署）

服务器上跑的是哪份代码，目前只能靠"我记得上次传的是哪个包"。
建议在构建时写入 `BUILD_INFO`（git sha + 构建时间 + 迁移版本号），并由 `/api/health/ready` 或 `/api/options` 暴露，
这样 smoke 可以直接断言"跑的是预期 commit"。改动小，收益是排障时间从"猜"降到"看"。

### 6.3 已知并接受（登记备查）

| 项 | 说明 |
|---|---|
| **IP 直访 + 内部 CA 自签证书** | 无域名场景下 `DR_DOMAIN=https://<公网IP>` + `DR_DEFAULT_SNI=<公网IP>`；客户端需信任根证书或点警告继续。<br>⇒ **需求 24（邮件重置）与 26（只读分享）发出的链接，外部浏览器会报证书错误，实际不可用**。内测可接受，对外部人分享前需先解决域名 |
| `qdrant/qdrant:latest` | 未固定 tag（compose 注释已记：上生产前应固定并记入变更记录）。其他服务均已钉版本 |
| Redis `allkeys-lru` | 队列是单个 list key（`lpush`/`brpop`）。LRU 淘汰的是 key 整体，非空队列被淘汰的概率极低（200 MB 上限对本用途很宽裕），且 PG 是权威 + `sweep_stale_runs` 30s 兜底 ⇒ 风险低。若要彻底消除可改 `noeviction` |
| Worker 700M 内存上限 | 长任务 state 累积可能触顶被 OOM kill ⇒ run 转 `LOST`。内测阶段观察即可 |
| 对象存储回落 PG | 未配 `DR_S3_*` 时报告存 PostgreSQL，功能正常但表会持续增大 |
| SMTP / GlitchTip 未启用 | 密码找回靠管理员 CLI 兜底；**前端白屏与未捕获异常目前零感知** |
| 无 CDN / WAF / 多实例编排 | 单点。内测规模可接受，公开版前需重评估 |

## 7. 变更记录

| 日期 | 改动 | 关联 |
|---|---|---|
| 2026-10-07 | 初版落库：整理主理人口述的部署形态，并与 `docker-compose.staging.yml` / `Caddyfile` 逐项核对；补充 §6 缺口清单 | 本次 |
