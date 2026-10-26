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
# 可选加密（需求 20 §8，推荐非对称）：BACKUP_RECIPIENT_CERT=<公钥.crt> bash tools/backup.sh
#   → 产出 deepresearch_*.dump.pem（openssl smime -aes256；服务器只存公钥，私钥离线保管）
# 对称备选：BACKUP_ENCRYPT=1 BACKUP_PASSPHRASE=...（口令仅环境变量注入、异地托管）
# 每日自动：tools/backup-cron.sh（备份 + rclone 同步 COS），cron 03:30，见脚本头注释
# 独立校验（不必重新备份）：tools/backup-verify.sh —— 新鲜度 + pg_restore -l；
#   .enc/.pem 提供口令/私钥时做解密校验，未提供则显式输出 structure-only
# 云上一次性安装与恢复演练检查单：见 §10（需求 1，2026-10-07）

# 2.4 配置 diff 核对：CORS / 预算 / 并发 / 限流 / 境外服务开关 / webhook
docker compose -f docker-compose.staging.yml config | grep -E "DR_(CORS|MONTHLY|MAX_CONCURRENT|ALERT|S3|ENV)"

# 2.4b 对象存储兼容核对（腾讯云 COS，2026-10-01 现场验证）：
#   - DR_S3_ADDRESSING_STYLE 必须为 virtual（auto 会选 path-style 被 PathStyleDomainForbidden 拒绝）
#   - 桶级 PutBucketLifecycleConfiguration 依赖 objectstore 的 Content-MD5 注入钩子
#     （见 web/backend/objectstore.py 模块 docstring 兼容表；缺失会报 InvalidRequest: Missing required header）
#   - MinIO/OSS 场景按 docstring 兼容表核对，不要照抄 COS 结论

# 2.4c 邮件通道核对（需求 24）：
#   - DNS：SPF（含服务商 include）、DKIM（服务商公钥）、DMARC（p=none 起步）；发件域名与 DR_SMTP_FROM 一致
#   - 环境变量：DR_SMTP_HOST / DR_SMTP_PORT / DR_SMTP_USER / DR_SMTP_PASSWORD / DR_SMTP_FROM / DR_SMTP_TLS
#     （ssl=465 默认；starttls=587；none=本地 MailHog）；DR_MAIL_BASE_URL 指向实际前端地址
#   - 未配置时 /api/auth/forgot 返回 mail_unavailable 503（管理员 CLI create-reset-token 兜底）
#   - 本地联调：docker compose -f docker-compose.staging.yml --profile local-mail up -d mailhog
#   - 上线 smoke：真实邮箱申请 → 收信（含垃圾箱检查）→ 完成重置 → 确认全设备会话吊销

# 2.4d 错误追踪核对（需求 25）：
#   - 默认形态：自托管 GlitchTip（compose profile errors；复用现有 postgres，独立 glitchtip 库）
#     首次启用：docker compose -f docker-compose.staging.yml --profile errors up -d glitchtip glitchtip-db-init
#     控制台建项目后，把 DSN 配到 DR_SENTRY_DSN（后端）与 DR_SENTRY_DSN_FRONTEND（前端经 /api/options 下发）
#   - 环境/发版：DR_SENTRY_ENVIRONMENT=staging、DR_SENTRY_RELEASE=<git SHA>（部署时注入）
#   - 注入演练：前端触发一次渲染异常 + 后端触发一次 500，确认错误服务控制台可达（1~2 分钟延迟属正常）
#   - 合规：隐私政策已含「错误诊断数据」小节；数据流向登记表已加行（境内自托管，不含内容/PII）

# 2.4e 报告分享 smoke（需求 26）：
#   - 确认 DR_SHARE_ENABLED=true（灰度开启）；DR_SHARE_RATE_PER_MINUTE 默认 30
#   - smoke：报告页创建分享（7 天）→ 匿名浏览器打开 /s/<token> 可见 → 撤销 → 同一链接 404
#   - 响应头核对：Cache-Control: private, no-store / X-Robots-Tag: noindex, nofollow, noarchive /
#     Referrer-Policy: no-referrer

# 2.5 容量检查：连接预算（capacity-model §3.1）
# N_api×DR_PG_POOL_MAX + N_worker×DR_PG_POOL_MAX + LISTEN + 余量 <= PG max_connections
```

## 3. 发布步骤（T-0）

### 3.0 自动部署（推荐；2026-10-13 起）

```bash
# 链路：dev 合入 ⇒ CI 6/6 绿 ⇒ 「Deploy staging」workflow 自动 SSH 触发服务器侧脚本
#   （也可在 Actions 页手动 workflow_dispatch，或服务器上直接执行）
ssh ubuntu@<STAGING_HOST> "bash /opt/deepresearch/tools/deploy.sh"   # 手动补部署（真实 host 见本地运维笔记，不入库）
bash tools/deploy.sh <sha|branch>                                    # 指定提交
bash tools/deploy.sh --rollback rollback-<sha>                       # 应用回滚（仅镜像；DB 不动）

# 脚本做什么（失败即中止，旧版本保持在线）：
#   前置检查 → 新鲜备份+COS → 回滚镜像标记（rollback-<prev-sha>）→ 源码快照
#   → git 同步（HTTP/1.1+重试）→ 构建（latest + SHA tag）→ 迁移（幂等 ×2）
#   → 滚动 worker→api（--wait）→ 冒烟（本机+公网 readiness / checkpoint 探测 / worker 异常扫描）
# 记录：$APP_DIR/deployments.log（时间 / 提交 / 结果）；回滚命令随失败信息打印。
#
# 失败重试（2026-10-14 起）：workflow 部署步骤内置「延迟自动重跑」——失败后 600s 重试，
#   最多 3 次尝试（覆盖大陆服务器→GitHub 偶发断流等瞬时故障；每次失败均安全中止、
#   旧版本保持在线，脚本幂等可重复执行）；连续 3 次失败才需人工介入（上方手动补部署 / 回滚）。
#   注：deploy.sh 始终部署 origin/dev 最新，延迟重跑天然补上落后版本。
#
# 安全口径：部署凭据只存 GitHub Secrets —— `DEPLOY_SSH_KEY`（SSH 私钥）、
#   `STAGING_HOST`（主机名/IP）、`STAGING_HOST_KEY`（host key，运行时写入 known_hosts）；
#   仓库与文档不写真实 host（`tools/check_public_ip.py --strict` 强制）。
#   服务器侧以 forced command 限定为只能执行本脚本（见 .github/workflows/deploy-staging.yml 头注释）；
#   公网冒烟：服务器 .env 配 `DR_PUBLIC_URL`；未配则跳过公网 readiness（WARN，本机仍强制）。
#   ⚠️ 能合入 dev 即具备 staging 部署能力 —— 勿在 dev 上合并未审代码。
```

### 3.1 手工步骤（fallback；与脚本同序，供排障/首次引导）

```bash
# 拉取发布提交并构建镜像（tag = git SHA）
git fetch origin && git checkout <release-sha>
export DR_IMAGE_TAG=$(git rev-parse --short HEAD)
docker compose -f docker-compose.staging.yml build

# 迁移（单事务批量执行 + 结构断言；失败即停止）
docker compose -f docker-compose.staging.yml up -d --wait postgres redis minio
docker compose -f docker-compose.staging.yml run --rm migrate
docker compose -f docker-compose.staging.yml run --rm migrate   # 二次执行应为幂等空跑

# 先起 Worker，再切 API（队列模式下新 Worker 可消费旧任务）
docker compose -f docker-compose.staging.yml up -d --wait worker
docker compose -f docker-compose.staging.yml up -d --wait api
```

## 4. 冒烟（新版本 5 分钟内完成）

```bash
BASE=https://<staging-host>
curl -fsS $BASE/api/health/ready | jq '.ok, .checks'          # readiness 全绿

# 安全响应头（P0-3）：CSP / HSTS / nosniff / frame-ancestors 必须存在
curl -sI $BASE/ | grep -Ei 'content-security-policy|strict-transport-security|x-content-type-options|referrer-policy'

# 反代信任（P0-1）：同一客户端伪造不同 XFF 连打 11 次登录（每次随机邮箱）=> 第 11 次应 429；
# 两个不同公网出口应各自独立计数；`admin audit-list` 里的 ip 必须是真实出口 IP。

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

### 7.1 公网入口（`proxy` profile，Caddy）

```bash
# 启动（服务器侧常驻；restart: unless-stopped + Docker 开机自启）
docker compose -f docker-compose.staging.yml --profile proxy up -d
docker ps --format 'table {{.Names}}\t{{.Status}}' | grep caddy   # 应显示 (healthy)

# 防火墙须放行 TCP 80 / 443（腾讯云轻量：控制台 → 实例 → 防火墙）
```

**两种模式（`.env`）**

| 模式 | 配置 | 证书 | 备注 |
|---|---|---|---|
| 域名（正式） | `DR_DOMAIN=example.com` | Let's Encrypt 自动签发/续期 | 大陆服务器**必须先完成 ICP 备案**，否则 80 被 webblock、443 按 SNI reset |
| IP 直访（备案前过渡） | `DR_DOMAIN=https://<公网IP>` + `DR_DEFAULT_SNI=<公网IP>` | Caddy 内部 CA（自签） | 浏览器访问 IP 不发 SNI，必须 `default_sni` 回退；客户端需信任根证书或用「继续前往」 |

```bash
# 根证书导出与信任（仅 IP 直访模式需要）
docker compose -f docker-compose.staging.yml exec caddy \
  cat /data/caddy/pki/authorities/local/root.crt > caddy-root.crt
# Windows（当前用户，免管理员）：certutil -user -addstore Root caddy-root.crt
# macOS：sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain caddy-root.crt
# iOS / Android：把 caddy-root.crt 发到设备安装，并在「证书信任设置」里启用

# 验证（IP 直访模式；schannel 对本地 CA 默认查吊销，curl 用 best-effort）
curl --ssl-revoke-best-effort -fsS https://<公网IP>/api/health/ready
```

**排错**
- 无 SNI 时 `tlsv1 alert internal error`：`DR_DEFAULT_SNI` 未透传进容器 —— `docker compose exec caddy env | grep DR_DEFAULT_SNI`；
- 改了 `Caddyfile` 不生效：bind mount 不触发容器重建，需 `docker compose -f docker-compose.staging.yml restart caddy`；
- 域名 302 到 `dnspod.qcloud.com/static/webblock.html` 或 TLS 被 reset：域名未备案（大陆云拦截）；
- caddy `unhealthy`：`docker compose logs caddy`；healthcheck 探的是容器内 admin API `:2019`。

### 7.2 跨进程恢复（需求 35 R3：resumed / replayed / fresh）

**三语义（勿混用）**

| 语义 | 触发 | 行为 | 计费 |
|---|---|---|---|
| SSE 回放 | 客户端带 `Last-Event-ID` 重连 | 从 `run_events` 按 sequence 补发（不重跑、不重算） | 不计 |
| 从节点恢复（`resumed`） | Worker 接管时 checkpoint 存在且版本兼容 | 从最后完成节点续跑；预算按 `usage_ledger` 汇总对账后注入 | 续跑新增调用如实累计 |
| 降级重跑（`replayed`） | checkpoint 缺失 / 版本不兼容 | **从头重跑**（不算恢复成功，如实标注） | 全部调用重复累计（账本含 attempt） |
| 首跑（`fresh`） | attempt=1 且无 checkpoint | 正常执行 | 正常 |

- **幂等边界 = 节点边界**：恢复后未完成节点会**重执行**（LLM 调用不可幂等）——重复调用如实记账；
- **checkpoint 存储**：`langgraph-checkpoint-postgres`（`thread_id=run_id`；库自管 schema——`checkpoints` /
  `checkpoint_blobs` / `checkpoint_writes`，Worker 首启幂等建表；**不入本项目 `migrations/`**）；
- **预算对账规则（TBD-4）**：`usage_ledger` 为累计费用事实源——恢复时若账本汇总 > checkpoint 内
  `token_used`（存在未完成节点的调用），以账本口径注入 state，**恢复路径预算不低估**；
- **兼容策略**：`ResearchState.checkpoint_schema_version` 与 `CHECKPOINT_SCHEMA_VERSION` 比对，不一致 ⇒ `replayed`；
- **执行模式留痕**：`RUN_STARTED` 事件与终局 payload 均含 `execution_mode` / `budget_reconciliation`（可审计）；
- **降级口径**：PG checkpoint 不可用（未配置 / 连接失败）⇒ Worker 回落 MemorySaver，模式退化为
  `fresh` / `replayed`，**不阻塞主链路**；
- **演练**：kill worker → 租约清扫接管 → 断言 `resumed` 续跑（无重复已完成节点、预算不低估）；
  删 checkpoint / 改版本号 ⇒ 断言 `replayed` 显式标注（`reason=no_checkpoint` / `version_incompatible`）。

### 7.3 报告正文流式协议（需求 35 R4：REPORT_DELTA / REPORT_RESET）

**帧协议**（自定义扩展事件；`run_events` 按 sequence 回放，支持 `Last-Event-ID`）

| 事件 | payload | 语义 |
|---|---|---|
| `REPORT_DELTA` | `{text, offset}` | 正文增量；`offset` = 本轮（上次 reset 后）累计字符数 |
| `REPORT_RESET` | `{}` | 清空预览缓冲 |

**客户端重建规则（前端已按此实现）**
1. 预览 = **最近一次 `REPORT_RESET` 之后**所有 `REPORT_DELTA` 的顺序拼接；
2. 终局以 `RUN_FINISHED.result.report` **整段替换**（绝不把终局正文追加到预览之后）；
3. 断线重连带 `Last-Event-ID` ⇒ 续发缺失增量（offset 连续可校验）。

**服务端行为**
- 流式开始先发 `REPORT_RESET`（重试 / 重跑 / 接管后重放安全）；
- 每累计 `DR_REPORT_STREAM_FLUSH_CHARS`（默认 400）字符合并为一条 `REPORT_DELTA`；
  收尾与 reset 前强制冲刷（保证 `RUN_FINISHED` 之前全部正文已入事件流）；
- **半截失败**（已发增量后 LLM 断流）⇒ 再发 `REPORT_RESET` + 降级非流式重试
  （留痕 `non_stream_retry`）；重试再失败 ⇒ 兜底报告（既有路径）；
- 总开关 `DR_REPORT_STREAM_ENABLED=false` 完全回到节点级事件（无增量）。

**输出闸边界（P0-4 口径延伸）**：正文增量在审核决定产生前即发帧（发起者可见草稿）；
flagged / blocked 的**回放对 `REPORT_DELTA` 同样脱敏**（正文只在产物 / 管理通道），
查看 / 导出 / 分享仍被闸。

**测试**：`tests/test_report_stream.py`（chat_stream / sink / Writer 降级）、
`tests/test_report_events.py`（持久化粒度 / 断线重连 / 半截失败）、
`tests/test_output_gate.py::test_stored_stream_redacts_flagged_report_delta`（回放脱敏）、
前端 `src/lib/reportPreview.test.ts`。

### 7.4 HITL 交互协议（需求 36：澄清 / 计划审批的挂起与恢复）

**前置边界**：HITL 仅队列模式支持（`DR_EXECUTION_MODE=queue` + Worker）；API 进程在
非队列模式启动时**强制关闭**开关并打印告警（进程内执行器没有挂起/恢复通道）；
**CLI 入口（`cli.py`）与证据链冒烟工具（`tools/smoke_evidence_chain.py`）同口径**强制关闭
图上 HITL（clarify/plan_approval）并告警（追问不受影响；`graph.run()` 的 interrupt 防御保留为兜底）。
默认全关（`DR_HITL_CLARIFY / DR_HITL_PLAN_APPROVAL / DR_HITL_FOLLOWUP = false`，灰度按单开关启用）。

**状态与集合口径**（单点定义见 `web/backend/persistence.py`）

| 集合 | 成员 | 语义 |
|---|---|---|
| `ACTIVE_STATUSES` | CREATED / QUEUED / RUNNING / CANCEL_REQUESTED | 占执行并发槽（配额计数、租约清扫、SSE 尾随） |
| `AWAITING_STATUSES` | AWAITING_CLARIFICATION / AWAITING_PLAN_APPROVAL | 等待人类输入：**不持租约、不占并发槽**、不被清扫/启动兜底误杀 |
| `OPEN_STATUSES` | 两者之和 | 非终局的统一口径（快照 running / 归档守卫 / 终局 allowed_from） |

**挂起事务（Worker → 库，同事务）**：图在 interrupt 处产出挂起 step ⇒ Worker 调
`store.suspend_run`：`RUNNING → AWAITING_*` + 交互卡事件 + `interaction_id/round/payload`
落库 + **清空 worker/租约字段**。凭据失效 ⇒ `LeaseLostError`（放弃写入）；终局/取消
⇒ 返回 None 不写（`CANCEL_REQUESTED` 绝不挂起，执行侧照常取消收口）。

**恢复事务（API → 库，同事务）**：`POST /api/research/{run_id}/interaction`
（`{interaction_id, action, text}`；澄清 `reply`，审批 `approve|revise`）。
`interaction_id` 即幂等键：重复提交 ⇒ `already_answered`（不重复写事件）；旧卡片/已换代
⇒ 409 `interaction_conflict`；非等待态 ⇒ 409 `interaction_not_pending`。成功 ⇒
`AWAITING_* → QUEUED` + 回答事件 + resume payload；API 投递 Redis 唤醒（可丢，PG 轮询兜底）。
Worker 认领后以 `resumed` 执行：`Command(resume=answer)` 注入挂起节点（R3 的
`skip_leading` 语义复用，已完成节点不重放；账本对账同 R3 口径）。

**事件（`run_events`，SSE 断线重连按 sequence 重建卡片）**

| 事件 | 语义 |
|---|---|
| `CLARIFY_REQUESTED` / `PLAN_PROPOSED` | 交互卡（载荷 = 卡片：interaction_id/kind/round/expires_at/allowed_actions/content） |
| `CLARIFY_ANSWERED` / `PLAN_DECISION` | 用户回答（含 action/text；flagged 回放脱敏） |
| `CLARIFY_EXPIRED` / `PLAN_EXPIRED` | 等待到期留痕 |

**SSE 行为（TBD-6）**：发送交互卡后本次 SSE 连接**正常关闭**（等待态不在 `ACTIVE_STATUSES`），
不发终态帧；客户端经状态查询/重连（快照 `interaction` 字段）获知等待态。

**到期规则（TBD-3；Worker 清扫周期驱动 `expire_awaiting_runs`）**
- 澄清到期 ⇒ **空回答自动继续**（转 QUEUED + `CLARIFY_EXPIRED` 留痕；显式产品策略）；
- 计划审批到期 ⇒ **不自动批准** ⇒ `TIMED_OUT` + `stop_reason=approval_expired` +
  `PLAN_EXPIRED`（同事务结算预算预留）；等待的「过期」不伪装成 worker 执行超时。

**运维纪律**：① 不要在有待答任务时增删 HITL 开关（图拓扑随开关变化）；
② 等待时长不计入 `timeout_at`（权威时间戳 `awaiting_since/resumed_at`，不反复顺延）；
③ 开关值进 `config_snapshot`（`hitl_switches`，eval/CI 强制关闭以保评测可比性）。

**测试**：`tests/test_hitl_protocol.py`（协议/脱敏/上限）、`tests/test_hitl_graph.py`
（关闭态零漂移 / 澄清与审批回路 / run() 防御）、`tests/test_hitl_worker.py`
（挂起释放租约 / 恢复携带回答值）、`tests/test_hitl_interaction_pg.py`（真 PG 原子协议）、
`tests/test_hitl_resume_drill_pg.py`（真 PG「挂起-退出-回答-恢复」演练）。

**追问（H3，`POST /api/research/{run_id}/follow-up`）**：报告后基于**已有证据池**回答
（不触发检索/图/新 run）。前置：`DR_HITL_FOLLOWUP=true` + 所有权 + CSRF + 限流
（`DR_FOLLOWUP_RATE_PER_MINUTE`，默认 10/分）+ run 终局且有报告 + 输出 gate（flagged 阻断）。
上下文只含 `report_md`（截断）与 `evidence_manifest` 有序清单（可引用 ID）；
回答结构化为 `{answer, citations, unverified_ids}`，服务端**零模型**校验引用 ID 逐字在池
（池外入 `unverified_ids`，不冒充已 grounded）。记账：`usage_ledger`（role=follow_up）+
本 run 成本增量（月度预算同源）；`FOLLOWUP_ASKED/ANSWERED` 进 `run_events`（flagged 回放脱敏）；
失败只影响本次追问（`followup_unavailable`），不改原 run 终态与报告。

### 7.5 MCP 工具面协议（需求 37：M1 消费 / M2 对外）

**M1 消费（外部 MCP → researcher）**：`DR_MCP_CONSUME_ENABLED=true` 时按 `DR_MCP_SERVERS`
（JSON）配置只读检索源；每跳并入 `mcp` 工具位（关闭态零漂移）。失败映射既有五值（零新增）；
成功/失败均记账（`kind='tool'`，per_call）；终点进 egress 快照；URL 仅 https、私网需
`allow_private`+理由（显式放行，审计）；结果清洗/截断（`max_result_bytes`/`max_findings`）。
配置非法 ⇒ 启动时 fail-fast；SDK 不可用 ⇒ `not_configured`（不 crash）。

**M2 对外（本项目 → MCP Client）**：`DR_MCP_SERVER_ENABLED=true` 时 API 进程挂载 `/mcp`
（Streamable HTTP；`json_response + stateless`；TLS 由反代终止，见 §7.1）。
鉴权：`Authorization: Bearer <DR_MCP_TOKEN>`（常量时间比较；**只认头**，query-string 一律拒绝；
未配置令牌 ⇒ fail-closed 全部 401）。单一 service principal `mcp:<DR_MCP_SERVER_NAME>`（默认
`mcp:ikaoju`，2026-10-16 品牌更名起；旧默认 `mcp:deepresearch`）：MCP 创建的 run 归属该主体，
status/report 仅本主体可见（不存在与无权同口径）。**更名影响**：未显式配置 `DR_MCP_SERVER_NAME`
的环境（含 staging）在下次 API 重启后主体变为 `mcp:ikaoju`（惰性开户），更名前创建的
`mcp:deepresearch` 旧 run 不再对该主体可见（运营如需可显式设回旧名）。
调用限流：`DR_MCP_RATE_PER_MINUTE`（默认 60/分/主体；多实例经 `DR_REDIS_URL` 共享）。
**工具面（v1，schema 版本 `mcp-tools/v1`）**：

| 工具 | 语义 | 关键限制 |
|---|---|---|
| `quick_search(query, max_results)` | 同步检索（provider 直调，不建 run）；记账 `kind='search'` | query ≤500 字符；max_results ≤10；返回限 title/url/snippet（≤500） |
| `deep_research(topic, instructions, profile, idempotency_key)` | 创建 run（**与 Web 同一准入/配额/审核/注入/幂等口径**），立即返回 `{run_id, status_url}` | topic ≤500 / instructions ≤2000；幂等键缺省按主体+内容派生 |
| `research_status(run_id)` | 状态快照（status / run_status / topic / has_report / stop_reason） | 仅本主体 |
| `research_report(run_id, format)` | 报告取回（**输出 gate 与 Web 导出同口径**：flagged/under-review 阻断） | md ≤200k 截断+标记；json >500k 报 `report_too_large` |

**运维要点**：调用全审计（`mcp_tool_called`：主体/工具/run_id/请求哈希/决策；**不落原始 prompt**）；
令牌轮换 = 更新 `DR_MCP_TOKEN` 并重启 API（无状态会话，无需清理）；关闭开关 = 卸载入口，零残留
（`config_snapshot` 记录双开关 + server 指纹）。端点兼容：`/mcp` 无尾斜杠 → 307 到 `/mcp/`
（SDK 客户端自动跟随；Starlette 1.6 的 Mount 仅匹配带尾斜杠路径）。service principal 在首次
`deep_research` 时**惰性开户**（`users` 行：邮箱 `<name>@mcp.invalid`、随机口令 Argon2id ⇒
不可登录；开户事件进审计；`runs.user_id` 外键完整性所需）。请求体上限由反代保障（未显式配置时
按平台默认），工具入参另有硬上限（上表）。**开启后冒烟**：

```bash
curl -fsS -X POST https://<host>/mcp/ -H "Authorization: Bearer $DR_MCP_TOKEN" \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

### 7.6 PDF 导出作业（需求 38 F2：异步作业 + 专用结果通道）

**服务拓扑**：`export`（700MB；单并发 supervisor，`python -m web.backend.export_worker`）+
`export-redis`（结果通道；**非持久化**：禁 AOF/RDB、`maxmemory 256mb`、`noeviction`）。
作业事实源在 PostgreSQL `export_jobs`（迁移 `0025`）；主 Redis 仅唤醒信号（丢失无害，2s 轮询兜底）。
API 侧端点：`POST /api/research/{run_id}/export-jobs`（202 + `export_id`；幂等复用）→
`GET …/export-jobs/{export_id}`（状态）→ `GET …/export-jobs/{export_id}/download`
（每次复核 run 所有权 + 输出 gate + 结果有效期；`application/pdf` + `no-store`）。

**配置（环境变量，缺省即默认值）**：

| 变量 | 默认 | 说明 |
|---|---|---|
| `DR_EXPORT_REDIS_URL` | 空（fail-fast） | 结果通道（compose 固定为 `redis://export-redis:6379/0`）；API 与 export 两侧都必须配置 |
| `DR_EXPORT_PDF_MAX_INPUT_BYTES` | 2MiB | 装配后输入上限（API 前置早拒 + export 复检） |
| `DR_EXPORT_PDF_MAX_PAGES` / `MAX_OUTPUT_BYTES` | 100 / 20MiB | 成品约束（超限 ⇒ 作业 FAILED） |
| `DR_EXPORT_PDF_TIMEOUT_SECONDS` | 30 | 单次渲染硬超时（超时硬杀渲染子进程重建，作业重排） |
| `DR_EXPORT_RESULT_TTL_SECONDS` | 900 | 结果 TTL（从完成起算，**下载不续期**） |
| `DR_EXPORT_RESULT_BUDGET_BYTES` | 192MiB | 结果累计预算（写满 ⇒ `export_cache_full`，**不驱逐**未过期结果） |
| `DR_EXPORT_RECYCLE_EVERY_JOBS` / `_MEMORY_MB` | 25 / 600 | 渲染子进程回收：每 N 作业或容器 `memory.current` 超阈值 |

**排障速查**：

- 作业长期 `QUEUED`：export 服务未起 / 健康检查失败 → `docker compose ps export`；
  看 `docker compose logs export`（认领/租约/回收均有日志）。
- 作业 `RUNNING` 卡住：租约 60s 过期后自动回收重排（`attempt ≤ 2` 后 `FAILED
  export_lease_expired`）；确认 export 容器存活即可自愈。
- `export_cache_full`（终态 FAILED）：结果 Redis 达预算 ⇒ 等 TTL 过期后让用户重新申请；
  勿改 `noeviction`（驱逐会丢未下载结果）。
- `export_result_expired`（下载 410）：TTL 已过/结果丢失 ⇒ 用户重新申请即再生成（幂等复用仅限未过期结果）。
- 内存增长：export 容器 `memory.current` ≥600MB 或每 25 作业自动回收渲染子进程；
  若持续接近 700MB 限额，先降 `DR_EXPORT_PDF_MAX_PAGES` 再评估限额（勿直接抬到研究 worker 量级）。
- 重建/回滚：`export` 与 `export-redis` 均为无状态可重建；`export-redis` 重启会清空未下载结果
  （**预期行为**：用户侧结构化过期错误 + 重新申请；作业状态不丢，因权威在 PG）。

### 7.7 报告导出端点（需求 38：md / json / docx 同步；PDF 作业见 §7.6）

`GET /api/research/{run_id}/report?format=md|json|docx`（P4-A：仅本人 run；P0-4：输出 gate
`flagged` / `under_review` / `blocked` 一律 403，与页面查看同口径）：

- `md`：Markdown（审计元数据头 + 正文 + 引用清单 + 访问来源；RFC 6266 话题名文件名）；
- `json`：结构化载荷（程序处理；既有契约不变）；
- `docx`（需求 38 F3）：python-docx **同步生成**（结构等价：标题层级/引用清单/附录；
  东亚字体钉 `w:eastAsia`=宋体），轻量离线、无作业态；失败 ⇒ 结构化错误（md/json 永远可用）。

排障：三个同步格式共用同一装配（内存未命中回落任务库产物；历史 run 缺 `export_json`
⇒ 引用/来源显式「不可用」）；`report_not_ready`（未终局）/ `report_unavailable`（无产物）
均为结构化错误，非 5xx。PDF 的异步链路与配置见 §7.6。

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
| 云上恢复演练（staging，临时容器） | ✅ 已执行（2026-10-11） | 离线私钥解密（208 条目）→ 云上临时容器恢复 → 断言与生产一致（schema_migrations/runs/run_artifacts = 21/11/22）；记录见 §10.3 |
| 应用版本回滚 | ⏳ 未演练（staging 执行后回填本节） | 本文 §6.1 为流程；回滚能力验证前，发布窗口需预留人工介入 |
| 迁移失败中断 | ⏳ 未演练 | 依赖 migrate 单事务语义；可在 staging 用损坏 fixture 演练 |

## 10. 云上备份安装与恢复演练（一次性；需求 1，2026-10-07）

> 背景：备份/恢复/加密脚本已入仓（PR #93/#94），CI 每次 PR 跑「备份→清库→恢复」，
> 但**云上未安装备份 cron、未做真实恢复演练**（`deployment.md` §6.1 / 上文 §9）。
> 判据：`pg_restore -l` 能列出目录才算成功；**未验证的备份视为不存在**。
> **✅ 2026-10-11：§10.1 全部核实完成、§10.2 演练已执行（记录见 §10.3）—— 本节转为留档检查单（重装时仍适用）。**

### 10.1 一次性安装（服务器 root）

- [x] 1. `backup-recipient.crt` 公钥放 `/opt/deepresearch/`；**私钥离线保管**（当前在开发机 `local-artifacts/deploy/dr-backup.key`，⚠️ 建议移入密码管理器/离线介质），服务器不留（需求 20 §8）
- [x] 2. rclone COS 配置就绪：`/root/.config/rclone/rclone.conf`（remote 名默认 `cos`）
- [x] 3. 安装 cron：`30 3 * * * /opt/deepresearch/tools/backup-cron.sh >> /var/log/dr-backup.log 2>&1`
- [x] 4. 手动首跑：`bash /opt/deepresearch/tools/backup-cron.sh` → 日志出现 `backup + COS sync ok`
- [x] 5. 校验：`BACKUP_DIR=/opt/deepresearch/backups bash /opt/deepresearch/tools/backup-verify.sh`
      （默认 structure-only；持有离线私钥的机器可做 decrypted 校验）—— 2026-10-11 结构校验通过（新鲜度 + md5 输出）
- [x] 6. COS 侧核对：对象存在、大小与本地一致；md5 一致（`rclone md5sum cos:<bucket>/backups/<文件>` 与 `backup-verify.sh` 输出的 md5 比对；对象为分片上传无 md5 时，回退记录本地 md5 供抽验）—— 2026-10-11 **三方一致**
- [x] 7. 失败可见：`/var/log/dr-backup.log` 可查；webhook 未配置时在告警登记中备注（P2-6）

### 10.2 恢复演练（建议临时容器执行，不碰生产库）

```bash
# 1) 取最新加密备份并用离线私钥解密（在持有私钥的安全机器上）
latest=$(ls -1t /opt/deepresearch/backups/deepresearch_*.dump.pem | head -1)
openssl smime -decrypt -in "$latest" -inkey /path/offline/recipient.key -out /tmp/drill.dump
pg_restore -l /tmp/drill.dump > /dev/null            # 先过判据

# 2) 起临时库并恢复
docker run -d --name dr-drill -e POSTGRES_PASSWORD=drill -e POSTGRES_USER=deepresearch \
  -e POSTGRES_DB=deepresearch postgres:16-alpine
docker exec -i dr-drill pg_restore --no-owner --no-privileges -U deepresearch -d deepresearch < /tmp/drill.dump

# 3) 断言（迁移记账 / 任务 / 产物）
docker exec dr-drill psql -U deepresearch -d deepresearch -tAc "SELECT count(*) FROM schema_migrations"
docker exec dr-drill psql -U deepresearch -d deepresearch -tAc "SELECT count(*) FROM runs"
docker exec dr-drill psql -U deepresearch -d deepresearch -tAc "SELECT count(*) FROM run_artifacts"

# 4) 清理
docker rm -f dr-drill; rm -f /tmp/drill.dump
```

> 如需「越权核验」全链路（删除 → 恢复 → 越权被拒），在 staging 维护窗口按
> `production-readiness.md` §5 执行；临时容器演练覆盖数据可恢复性。

### 10.3 演练记录（执行后回填，并同步 §9 状态）

| 项 | 值 |
|---|---|
| 演练日期 / 执行人 | 2026-10-11 23:10~23:20（CST）/ opencode（主理人授权，经 `ubuntu@<STAGING_HOST>`） |
| 备份点（文件 / 时间 / 大小 / md5） | `deepresearch_20261010_193001.dump.pem`（2026-10-11 03:30 CST 生成；220,327 B；md5 `e4e54e1091fa4ea1f9404d53b10ba146`） |
| 产物形态（明文 / .enc / .pem） | .pem（openssl smime 非对称）→ 离线私钥解密成功（明文 161,871 B；`pg_restore -l` 208 条目） |
| 解密与 `pg_restore -l` | 通过 |
| 恢复耗时（下载 + 解密 + 恢复 + 断言） | ≈2 分钟（下载+解密+判据 ≈1 分钟；容器启动→就绪→流式恢复→断言 ≈1 分钟） |
| 断言（schema_migrations / runs / run_artifacts） | **21 / 11 / 22**（与生产库逐项一致） |
| 失败项与改进项 | 无失败。① 明文经 ssh stdin **流式恢复、未落服务器磁盘**；② 离线私钥现位于开发机 `local-artifacts/deploy/dr-backup.key`，⚠️ 建议移入密码管理器/离线介质；③ 顺手修复服务器 `tools/backup-verify.sh` 事故空目录（历史 docker `-v` 挂载缺失文件所致）→ 已替换为仓库版（md5 一致） |
| 结论（可恢复 / 不可恢复） | **可恢复** |
