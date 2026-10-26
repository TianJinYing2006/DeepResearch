#!/usr/bin/env bash
# staging 一键自动部署（2026-10-13，需求 35 收口后新增；2026-10-26 加阶段耗时/超时）。
#
# 把 origin/dev（或指定 ref）安全地部署到本机 staging：
#   前置检查 → 新鲜备份(+COS) → 回滚镜像标记 → 源码快照 → git 同步（重试）
#   → 构建（tag=latest + SHA） → 迁移（幂等 ×2） → 滚动 worker→api→export → Caddy reload → 冒烟
#
# 用法（服务器上，ubuntu 用户；免密 sudo 必需）：
#   bash tools/deploy.sh                    # 部署 origin/dev 最新（CI 触发路径）
#   bash tools/deploy.sh <sha|branch|tag>   # 部署指定提交（浅拉取）
#   bash tools/deploy.sh --rollback <tag>   # 应用回滚（仅镜像；DB 不动，见 runbook §6.1）
#
# 环境变量（可选）：
#   DR_APP_DIR      部署目录（默认 /opt/deepresearch）
#   DR_PUBLIC_URL   公网冒烟地址（可选；真实 host 不入库）。未设置时读服务器 .env 的
#                   DR_PUBLIC_URL；仍无则跳过公网冒烟并 WARN（本机 readiness 仍强制执行）
#   SKIP_SAME=1     目标提交与当前相同时直接跳过（CI 重复触发用）
#   FORCE=1         与 SKIP_SAME 同提交时的显式重部署
#
# 由 GitHub Actions「Deploy staging」workflow 触发（CI 绿后自动；手动 dispatch 同链路）。
# 失败即退出非零并打印回滚命令；**不做自动回滚**（回滚决策留人，见 release-runbook §6）。
# 每个阶段打印耗时；慢阶段有超时（backup 900s / snapshot 300s / build 1800s /
# migrate 600s / rolling 300s / caddy reload 60s），超时按失败处理，不再无限等待。
set -euo pipefail

APP_DIR="${DR_APP_DIR:-/opt/deepresearch}"
PUBLIC_URL="${DR_PUBLIC_URL:-}"
if [ -z "$PUBLIC_URL" ] && [ -f "$APP_DIR/.env" ]; then
    PUBLIC_URL="$(sed -n 's/^DR_PUBLIC_URL=//p' "$APP_DIR/.env" | tail -n 1)"
fi
PUBLIC_URL="${PUBLIC_URL%\"}"
PUBLIC_URL="${PUBLIC_URL#\"}"
PUBLIC_URL="${PUBLIC_URL%/}"
COMPOSE_FILE="docker-compose.staging.yml"
LOCK_FILE="/tmp/dr-deploy.lock"
LOG_FILE="$APP_DIR/deployments.log"

# ---- 自快照重入：脚本运行期间 git checkout 会覆盖本文件（bash 惰性读取会读到半截）----
if [ "${DR_DEPLOY_CHILD:-0}" != "1" ]; then
    SNAP="/tmp/dr-deploy.$$.sh"
    cp "$0" "$SNAP"
    DR_DEPLOY_CHILD=1 exec bash "$SNAP" "$@"
fi
trap 'rm -f "/tmp/dr-deploy.$$.sh"' EXIT

START_TS="$(date +%s)"
STEP_NAME=""
STEP_TS="$START_TS"

log()  { echo "[deploy $(date "+%F %T")] $*"; }
die()  { log "ERROR: $*"; exit 1; }
record() { printf "%s\t%s\t%s\n" "$(date "+%F %T")" "$1" "$2" >> "$LOG_FILE" 2>/dev/null || true; }

step_start() {
    STEP_NAME="$1"
    STEP_TS="$(date +%s)"
    log "== $STEP_NAME =="
}
step_done() {
    log "== $STEP_NAME 完成（$(( $(date +%s) - STEP_TS ))s）=="
}
# 给关键阶段加超时；宿主无 timeout 命令时退化为无超时（记录 WARN）
run_timeout() {
    local secs="$1"
    shift
    if command -v timeout >/dev/null 2>&1; then
        timeout "$secs" "$@"
    else
        log "WARN 缺少 timeout 命令，阶段无超时保护"
        "$@"
    fi
}

cd "$APP_DIR" || die "部署目录不存在：$APP_DIR"

# ---- 串行化（CI 触发与手工操作并发时排队；拿不到锁即退出非零）----
exec 9>"$LOCK_FILE"
flock -n 9 || die "另一个部署正在进行（$LOCK_FILE）"

PREV_SHA="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
trap 'code=$?; if [ $code -ne 0 ]; then log "部署失败（exit=$code，总耗时 $(( $(date +%s) - ${START_TS:-0} ))s）。回滚：bash tools/deploy.sh --rollback rollback-$PREV_SHA"; record "rollback-$PREV_SHA" "fail"; fi; rm -f "/tmp/dr-deploy.$$.sh"' EXIT

# ---- 回滚模式（仅应用镜像；DB 不动）----
if [ "${1:-}" = "--rollback" ]; then
    TAG="${2:-}"
    [ -n "$TAG" ] || die "用法：deploy.sh --rollback <镜像tag>（如 rollback-13d40b9）"
    docker image inspect "deepresearch-staging-api:$TAG" >/dev/null 2>&1 || die "镜像 tag 不存在：$TAG"
    log "回滚应用镜像 → $TAG（DB 未动）"
    docker image tag "deepresearch-staging-api:$TAG" deepresearch-staging-api:latest
    docker image tag "deepresearch-staging-worker:$TAG" deepresearch-staging-worker:latest
    run_timeout 300 docker compose -f "$COMPOSE_FILE" up -d --wait worker
    run_timeout 300 docker compose -f "$COMPOSE_FILE" up -d --wait api
    # 需求 38 F2：export 与 api 共用镜像 tag ⇒ 同滚同回
    run_timeout 300 docker compose -f "$COMPOSE_FILE" up -d --wait export
    curl --max-time 15 -fsS http://127.0.0.1:8000/api/health/ready | grep -q '"ok":true' || die "回滚后 readiness 失败"
    record "rollback:$TAG" "ok"
    log "回滚完成（总耗时 $(( $(date +%s) - START_TS ))s）。前滚：bash tools/deploy.sh"
    exit 0
fi

TARGET="${1:-}"

step_start "0) 前置检查"
command -v docker >/dev/null || die "docker 未安装"
sudo -n true 2>/dev/null || die "需要 ubuntu 免密 sudo（备份步骤用）"
docker compose -f "$COMPOSE_FILE" config -q || die "compose 配置非法"
step_done

step_start "1) 新鲜备份 + COS 同步（失败即中止）"
run_timeout 900 sudo -n sh "$APP_DIR/tools/backup-cron.sh" >/dev/null || die "备份失败（见 /var/log/dr-backup.log）"
step_done

step_start "2) 回滚标记（当前镜像 → rollback-$PREV_SHA）"
docker image tag deepresearch-staging-api:latest "deepresearch-staging-api:rollback-$PREV_SHA" 2>/dev/null || true
docker image tag deepresearch-staging-worker:latest "deepresearch-staging-worker:rollback-$PREV_SHA" 2>/dev/null || true
step_done

step_start "3) 源码快照（含未跟踪文件 → backups/）"
STAMP="$(date +%Y%m%d_%H%M%S)"
run_timeout 300 sudo -n tar czf "$APP_DIR/backups/src-pre-$STAMP.tar.gz" \
    --exclude=./backups --exclude=./.git -C "$APP_DIR" . || die "源码快照失败"
step_done

step_start "4) git 同步（HTTP/1.1 + 重试；GitHub 自大陆服务器偶发断流）"
if [ ! -d .git ]; then
    git init -q
    git remote add origin https://github.com/TianJinYing2006/DeepResearch.git
fi
ok=0
for i in 1 2 3 4 5; do
    if git -c http.version=HTTP/1.1 fetch --depth=1 origin dev; then ok=1; break; fi
    log "fetch 第 $i 次失败，3s 后重试"
    sleep 3
done
[ "$ok" = 1 ] || die "git fetch 失败（网络；旧版本仍在运行）"
git checkout -f -B dev FETCH_HEAD
if [ -n "$TARGET" ]; then
    git -c http.version=HTTP/1.1 fetch --depth=1 origin "$TARGET" || die "指定 ref 拉取失败：$TARGET"
    git checkout -f FETCH_HEAD
fi
# git 不保存部分脚本的执行位（仓库 100644）⇒ 显式补齐（cron 每日备份直接执行 backup-cron.sh）
chmod +x tools/backup-cron.sh tools/backup.sh tools/backup-verify.sh tools/migrate.sh tools/deploy.sh 2>/dev/null || true
NEW_SHA="$(git rev-parse --short HEAD)"
log "目标提交：$NEW_SHA（上一版：$PREV_SHA）"
step_done

if [ "$NEW_SHA" = "$PREV_SHA" ] && [ "${FORCE:-0}" != "1" ]; then
    if [ "${SKIP_SAME:-0}" = "1" ]; then
        log "已是同一提交，SKIP_SAME=1 ⇒ 跳过"
        record "$NEW_SHA" "skip-same"
        exit 0
    fi
    log "已是同一提交；继续执行（幂等重建/迁移/冒烟）"
fi

step_start "5) 构建镜像（latest + $NEW_SHA）"
run_timeout 1800 docker compose -f "$COMPOSE_FILE" build api worker || die "构建失败/超时（旧版本仍在运行）"
docker image tag deepresearch-staging-api:latest "deepresearch-staging-api:$NEW_SHA"
docker image tag deepresearch-staging-worker:latest "deepresearch-staging-worker:$NEW_SHA"
step_done

step_start "6) 迁移（幂等 ×2；失败即中止且应用未切换）"
run_timeout 600 docker compose -f "$COMPOSE_FILE" up -d --wait postgres redis >/dev/null
run_timeout 600 docker compose -f "$COMPOSE_FILE" run --rm migrate || die "迁移失败/超时（应用未切换，旧版本仍在运行）"
run_timeout 600 docker compose -f "$COMPOSE_FILE" run --rm migrate | tail -1
step_done

step_start "7) 滚动：worker → api → export"
run_timeout 300 docker compose -f "$COMPOSE_FILE" up -d --wait worker || die "worker 启动失败/超时（旧 api 仍在运行）"
run_timeout 300 docker compose -f "$COMPOSE_FILE" up -d --wait api || die "api 启动失败/超时"
# 需求 38 F2：export 与 api 共用镜像 tag；api 的 depends_on 会先拉起 export-redis
run_timeout 300 docker compose -f "$COMPOSE_FILE" up -d --wait export || die "export 启动失败/超时"
step_done

step_start "7b) Caddy reload（Caddyfile 变更生效）"
# 精确按 compose service 找容器（不再用 grep 名字，避免误 reload 其它项目的 caddy）
CADDY_ID="$(docker compose -f "$COMPOSE_FILE" ps -q caddy 2>/dev/null | head -n 1 || true)"
if [ -n "$CADDY_ID" ]; then
    run_timeout 60 docker exec "$CADDY_ID" caddy reload --config /etc/caddy/Caddyfile \
        || die "Caddy reload 失败/超时（Caddyfile 未生效；请手动排查）"
    log "Caddy reload OK（container=${CADDY_ID:0:12}）"
else
    log "未发现运行中的 caddy 服务（proxy profile 未启用）⇒ 跳过 reload"
fi
step_done

step_start "8) 冒烟"
sleep 2
curl --max-time 15 -fsS http://127.0.0.1:8000/api/health/ready | grep -q '"ok":true' || die "本机 readiness 失败"
if [ -n "$PUBLIC_URL" ]; then
    curl --max-time 15 -sk --ssl-revoke-best-effort -fsS "$PUBLIC_URL/api/health/ready" | grep -q '"ok":true' || die "公网 readiness 失败"
else
    log "WARN DR_PUBLIC_URL 未配置（服务器 .env 或环境变量）；跳过公网 readiness"
fi
run_timeout 60 docker exec deepresearch-staging-worker-1 python -c \
    "import os; from web.backend.checkpoint import get_pg_checkpointer as g; assert g(os.getenv('DR_DATABASE_URL')), 'checkpoint 不可用'" \
    || die "checkpoint 运行时探测失败/超时"
docker logs deepresearch-staging-worker-1 --since 2m 2>&1 | grep -Ei "traceback|exception" && die "worker 近期有异常日志" || true
docker inspect --format '{{.State.Health.Status}}' deepresearch-staging-export-1 | grep -q healthy \
    || die "export 容器未就绪（需求 38 F2）"
docker logs deepresearch-staging-export-1 --since 2m 2>&1 | grep -Ei "traceback|exception" && die "export 近期有异常日志" || true
step_done

record "$NEW_SHA" "ok"
log "== 部署完成：$PREV_SHA → $NEW_SHA（总耗时 $(( $(date +%s) - START_TS ))s）=="
docker ps --format '{{.Names}} {{.Status}}' | grep deepresearch || true
