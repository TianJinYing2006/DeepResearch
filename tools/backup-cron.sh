#!/bin/sh
# 每日加密备份 + COS 异地同步（cron 入口；需求 20 §8 数据安全）。
#
# 安装（root；服务器一次性）：
#   30 3 * * * /opt/deepresearch/tools/backup-cron.sh >> /var/log/dr-backup.log 2>&1
#
# 依赖（见 docs/operations/release-runbook.md §2.3）：
#   - 公钥证书 /opt/deepresearch/backup-recipient.crt（**私钥必须离线保管，服务器不留**）；
#   - rclone 配置 /root/.config/rclone/rclone.conf（remote 默认 cos）。
#
# 可覆盖：DR_BACKUP_APP_DIR / DR_BACKUP_DIR / DR_BACKUP_REMOTE / DR_BACKUP_BUCKET / BACKUP_KEEP
#
# 同步口径（审计 #326，High）：COS 只接收
#   - 加密 DB 备份：deepresearch_*.dump.pem（非对称）/ deepresearch_*.dump.enc（对称）；
#   - 源码快照：src-pre-*.tar.gz（deploy.sh 打快照时已排除 .env* 与备份公钥）。
# 明文 deepresearch_*.dump / 回验临时文件（.verify* / .to_delete*）绝不同步——
# rclone 用 include 允许清单（未命中即不同步），即使脚本回归留下明文也不会外泄。
set -eu
APP_DIR="${DR_BACKUP_APP_DIR:-/opt/deepresearch}"
cd "$APP_DIR"

set -a
. ./.env
set +a

OUT="${DR_BACKUP_DIR:-$APP_DIR/backups}"
CERT="$APP_DIR/backup-recipient.crt"
REMOTE="${DR_BACKUP_REMOTE:-cos}"
BUCKET="${DR_BACKUP_BUCKET:-deepresearch-1498639479}"
# cron 以 root 运行；显式指定配置路径，避免 sudo/手工执行时 HOME 不一致
RCLONE_CONF="${RCLONE_CONFIG:-/root/.config/rclone/rclone.conf}"

[ -f "$CERT" ] || { echo "缺少公钥证书：$CERT（见 runbook §2.3）"; exit 1; }
: "${DR_POSTGRES_PASSWORD:?缺少 DR_POSTGRES_PASSWORD（.env）}"

NET=$(docker network ls --format '{{.Name}}' | grep -m1 '^deepresearch-staging' || true)
[ -n "$NET" ] || { echo "找不到 compose 网络（deepresearch-staging_*）"; exit 1; }

mkdir -p "$OUT"
# P0-4：连接参数用临时 env-file（0600 + trap 清理）传给容器 —— 口令不进 docker CLI argv/ps。
CONN_ENV="$(mktemp)"
chmod 600 "$CONN_ENV"
trap 'rm -f "$CONN_ENV"' EXIT
cat > "$CONN_ENV" <<EOF
PGHOST=postgres
PGPORT=5432
PGUSER=deepresearch
PGDATABASE=deepresearch
PGPASSWORD=${DR_POSTGRES_PASSWORD}
EOF
docker run --rm --network "$NET" \
    -v "$APP_DIR/tools/backup.sh:/b.sh:ro" \
    -v "$OUT:/backups" \
    -v "$CERT:/backup-recipient.crt:ro" \
    --env-file "$CONN_ENV" \
    -e BACKUP_RECIPIENT_CERT=/backup-recipient.crt \
    -e BACKUP_DIR=/backups \
    -e "BACKUP_KEEP=${BACKUP_KEEP:-14}" \
    -e BACKUP_REQUIRE_ENCRYPTION=1 \
    postgres:16 sh /b.sh

# 审计 #326：允许清单同步——只上传加密 DB 备份与源码快照（源码快照已排除 .env*）；
# 明文 dump / 临时文件一律不进 COS（--include 未命中即被排除）。
rclone --config "$RCLONE_CONF" copy --immutable \
    --include 'deepresearch_*.dump.pem' \
    --include 'deepresearch_*.dump.enc' \
    --include 'src-pre-*.tar.gz' \
    "$OUT" "${REMOTE}:${BUCKET}/backups/"
echo "[$(date -Is)] backup + COS sync ok"
