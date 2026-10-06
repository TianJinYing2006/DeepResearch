#!/bin/sh
# 任务库恢复（P8-A）：清空 public schema 后从备份恢复，并校验迁移记录可读。
#
# 用法（宿主机或运行 PG 的容器内）：
#   DATABASE_URL=postgresql://... tools/restore.sh [<dump 文件>]
#   缺省取 BACKUP_DIR（默认 /backups）里最新的 deepresearch_*.dump
#
# ⚠️ 会覆盖目标库的 public schema —— 只允许用于恢复演练或数据侧回滚。
set -eu

DATABASE_URL="${DATABASE_URL:?需要 DATABASE_URL}"
OUT_DIR="${BACKUP_DIR:-/backups}"

if [ "${1:-}" != "" ]; then
    BACKUP_FILE="$1"
else
    BACKUP_FILE="$(ls -1t "$OUT_DIR"/deepresearch_*.dump 2>/dev/null | head -n 1 || true)"
fi
if [ -z "${BACKUP_FILE:-}" ] || [ ! -f "$BACKUP_FILE" ]; then
    echo "找不到备份文件（BACKUP_DIR=$OUT_DIR）" >&2
    exit 2
fi

psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -q -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
pg_restore --no-owner --no-privileges -d "$DATABASE_URL" "$BACKUP_FILE"

migrations="$(psql "$DATABASE_URL" -tAc "SELECT count(*) FROM schema_migrations" 2>/dev/null || echo 0)"
if [ "$migrations" -lt 1 ]; then
    echo "恢复后 schema_migrations 为空，恢复可能失败" >&2
    exit 1
fi
echo "restore ok: $BACKUP_FILE（migrations=$migrations）"
