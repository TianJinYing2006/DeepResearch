#!/usr/bin/env bash
# staging 一键自动部署（2026-10-13，需求 35 收口后新增）。
#
# 把 origin/dev（或指定 ref）安全地部署到本机 staging：
#   前置检查 → 新鲜备份(+COS) → 回滚镜像标记 → 源码快照 → git 同步（重试）
#   → 构建（tag=latest + SHA） → 迁移（幂等 ×2） → 滚动 worker→api → 冒烟
#
# 用法（服务器上，ubuntu 用户；免密 sudo 必需）：
#   bash tools/deploy.sh                    # 部署 origin/dev 最新（CI 触发路径）
#   bash tools/deploy.sh <sha|branch|tag>   # 部署指定提交（浅拉取）
#   bash tools/deploy.sh --rollback <tag>   # 应用回滚（仅镜像；DB 不动，见 runbook §6.1）
#
# 环境变量（可选）：
#   DR_APP_DIR      部署目录（默认 /opt/deepresearch）
#   DR_PUBLIC_URL   公网冒烟地址（默认 https://82.156.88.246）
#   SKIP_SAME=1     目标提交与当前相同时直接跳过（CI 重复触发用）
#   FORCE=1         与 SKIP_SAME 同提交时的显式重部署
#
# 由 GitHub Actions「Deploy staging」workflow 触发（CI 绿后自动；手动 dispatch 同链路）。
# 失败即退出非零并打印回滚命令；**不做自动回滚**（回滚决策留人，见 release-runbook §6）。
set -euo pipefail

APP_DIR="${DR_APP_DIR:-/opt/deepresearch}"
PUBLIC_URL="${DR_PUBLIC_URL:-https://82.156.88.246}"
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

log()  { echo "[deploy $(date "+%F %T")] $*"; }
die()  { log "ERROR: $*"; exit 1; }
record() { printf "%s\t%s\t%s\n" "$(date "+%F %T")" "$1" "$2" >> "$LOG_FILE" 2>/dev/null || true; }

cd "$APP_DIR" || die "部署目录不存在：$APP_DIR"

# ---- 串行化（CI 触发与手工操作并发时排队；拿不到锁即退出非零）----
exec 9>"$LOCK_FILE"
flock -n 9 || die "另一个部署正在进行（$LOCK_FILE）"

PREV_SHA="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
trap 'code=$?; if [ $code -ne 0 ]; then log "部署失败（exit=$code）。回滚：bash tools/deploy.sh --rollback rollback-$PREV_SHA"; record "rollback-$PREV_SHA" "fail"; fi; rm -f "/tmp/dr-deploy.$$.sh"' EXIT

# ---- 回滚模式（仅应用镜像；DB 不动）----
if [ "${1:-}" = "--rollback" ]; then
    TAG="${2:-}"
    [ -n "$TAG" ] || die "用法：deploy.sh --rollback <镜像tag>（如 rollback-13d40b9）"
    docker image inspect "deepresearch-staging-api:$TAG" >/dev/null 2>&1 || die "镜像 tag 不存在：$TAG"
    log "回滚应用镜像 → $TAG（DB 未动）"
    docker image tag "deepresearch-staging-api:$TAG" deepresearch-staging-api:latest
    docker image tag "deepresearch-staging-worker:$TAG" deepresearch-staging-worker:latest
    docker compose -f "$COMPOSE_FILE" up -d --wait worker
    docker compose -f "$COMPOSE_FILE" up -d --wait api
    curl -fsS http://127.0.0.1:8000/api/health/ready | grep -q '"ok":true' || die "回滚后 readiness 失败"
    record "rollback:$TAG" "ok"
    log "回滚完成。前滚：bash tools/deploy.sh"
    exit 0
fi

TARGET="${1:-}"

log "== 0) 前置检查 =="
command -v docker >/dev/null || die "docker 未安装"
sudo -n true 2>/dev/null || die "需要 ubuntu 免密 sudo（备份步骤用）"
docker compose -f "$COMPOSE_FILE" config -q || die "compose 配置非法"

log "== 1) 新鲜备份 + COS 同步（失败即中止） =="
sudo -n sh "$APP_DIR/tools/backup-cron.sh" >/dev/null || die "备份失败（见 /var/log/dr-backup.log）"
log "备份 OK"

log "== 2) 回滚标记（当前镜像 → rollback-$PREV_SHA） =="
docker image tag deepresearch-staging-api:latest "deepresearch-staging-api:rollback-$PREV_SHA" 2>/dev/null || true
docker image tag deepresearch-staging-worker:latest "deepresearch-staging-worker:rollback-$PREV_SHA" 2>/dev/null || true

log "== 3) 源码快照（含未跟踪文件 → backups/） =="
STAMP="$(date +%Y%m%d_%H%M%S)"
sudo -n tar czf "$APP_DIR/backups/src-pre-$STAMP.tar.gz" \
    --exclude=./backups --exclude=./.git -C "$APP_DIR" . || die "源码快照失败"

log "== 4) git 同步（HTTP/1.1 + 重试；GitHub 自大陆服务器偶发断流） =="
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

if [ "$NEW_SHA" = "$PREV_SHA" ] && [ "${FORCE:-0}" != "1" ]; then
    if [ "${SKIP_SAME:-0}" = "1" ]; then
        log "已是同一提交，SKIP_SAME=1 ⇒ 跳过"
        record "$NEW_SHA" "skip-same"
        exit 0
    fi
    log "已是同一提交；继续执行（幂等重建/迁移/冒烟）"
fi

log "== 5) 构建镜像（latest + $NEW_SHA） =="
docker compose -f "$COMPOSE_FILE" build api worker || die "构建失败（旧版本仍在运行）"
docker image tag deepresearch-staging-api:latest "deepresearch-staging-api:$NEW_SHA"
docker image tag deepresearch-staging-worker:latest "deepresearch-staging-worker:$NEW_SHA"

log "== 6) 迁移（幂等 ×2；失败即中止且应用未切换） =="
docker compose -f "$COMPOSE_FILE" up -d --wait postgres redis >/dev/null
docker compose -f "$COMPOSE_FILE" run --rm migrate || die "迁移失败（应用未切换，旧版本仍在运行）"
docker compose -f "$COMPOSE_FILE" run --rm migrate | tail -1

log "== 7) 滚动：worker → api =="
docker compose -f "$COMPOSE_FILE" up -d --wait worker || die "worker 启动失败（旧 api 仍在运行）"
docker compose -f "$COMPOSE_FILE" up -d --wait api || die "api 启动失败"

log "== 8) 冒烟 =="
sleep 2
curl -fsS http://127.0.0.1:8000/api/health/ready | grep -q '"ok":true' || die "本机 readiness 失败"
curl -sk --ssl-revoke-best-effort -fsS "$PUBLIC_URL/api/health/ready" | grep -q '"ok":true' || die "公网 readiness 失败"
docker exec deepresearch-staging-worker-1 python -c \
    "import os; from web.backend.checkpoint import get_pg_checkpointer as g; assert g(os.getenv('DR_DATABASE_URL')), 'checkpoint 不可用'" \
    || die "checkpoint 运行时探测失败"
docker logs deepresearch-staging-worker-1 --since 2m 2>&1 | grep -Ei "traceback|exception" && die "worker 近期有异常日志" || true

record "$NEW_SHA" "ok"
log "== 部署完成：$PREV_SHA → $NEW_SHA =="
docker ps --format '{{.Names}} {{.Status}}' | grep deepresearch || true
