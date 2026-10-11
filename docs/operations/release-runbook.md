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
| 云上恢复演练（staging，临时容器） | ⏳ 待主理人执行（检查单见 §10） | 未执行前按「无备份」对待；CI 演练只代表 CI 环境 |
| 应用版本回滚 | ⏳ 未演练（staging 执行后回填本节） | 本文 §6.1 为流程；回滚能力验证前，发布窗口需预留人工介入 |
| 迁移失败中断 | ⏳ 未演练 | 依赖 migrate 单事务语义；可在 staging 用损坏 fixture 演练 |

## 10. 云上备份安装与恢复演练（一次性；需求 1，2026-10-07）

> 背景：备份/恢复/加密脚本已入仓（PR #93/#94），CI 每次 PR 跑「备份→清库→恢复」，
> 但**云上未安装备份 cron、未做真实恢复演练**（`deployment.md` §6.1 / 上文 §9）。
> 判据：`pg_restore -l` 能列出目录才算成功；**未验证的备份视为不存在**。

### 10.1 一次性安装（服务器 root）

- [ ] 1. `backup-recipient.crt` 公钥放 `/opt/deepresearch/`；**私钥离线保管**（密码管理器/离线盘），服务器不留（需求 20 §8）
- [ ] 2. rclone COS 配置就绪：`/root/.config/rclone/rclone.conf`（remote 名默认 `cos`）
- [ ] 3. 安装 cron：`30 3 * * * /opt/deepresearch/tools/backup-cron.sh >> /var/log/dr-backup.log 2>&1`
- [ ] 4. 手动首跑：`bash /opt/deepresearch/tools/backup-cron.sh` → 日志出现 `backup + COS sync ok`
- [ ] 5. 校验：`BACKUP_DIR=/opt/deepresearch/backups bash /opt/deepresearch/tools/backup-verify.sh`
      （默认 structure-only；持有离线私钥的机器可做 decrypted 校验）
- [ ] 6. COS 侧核对：对象存在、大小与本地一致；md5 一致（`rclone md5sum cos:<bucket>/backups/<文件>` 与 `backup-verify.sh` 输出的 md5 比对；对象为分片上传无 md5 时，回退记录本地 md5 供抽验）
- [ ] 7. 失败可见：`/var/log/dr-backup.log` 可查；webhook 未配置时在告警登记中备注（P2-6）

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
| 演练日期 / 执行人 | |
| 备份点（文件 / 时间 / 大小 / md5） | |
| 产物形态（明文 / .enc / .pem） | |
| 解密与 `pg_restore -l` | 通过 / 失败 |
| 恢复耗时（下载 + 解密 + 恢复 + 断言） | |
| 断言（schema_migrations / runs / run_artifacts） | |
| 失败项与改进项 | |
| 结论（可恢复 / 不可恢复） | |
