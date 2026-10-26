#!/bin/sh
# 任务库恢复（P8-A）：清空 public schema 后从备份恢复，并校验迁移记录可读。
#
# 用法（宿主机或运行 PG 的容器内）：
#   PGHOST=... PGPORT=... PGUSER=... PGPASSWORD=... PGDATABASE=... tools/restore.sh [<dump 文件>]
#   兼容：DATABASE_URL=postgresql://...（脚本内解析，不传给子进程 argv；口令特殊字符需 URL 编码）
#   缺省取 BACKUP_DIR（默认 /backups）里最新的 deepresearch_*.dump
#
# ⚠️ 会覆盖目标库的 public schema —— 只允许用于恢复演练或数据侧回滚。
set -eu

OUT_DIR="${BACKUP_DIR:-/backups}"

# 连接参数：优先 PG*；兼容 DATABASE_URL（解析到环境变量，避免口令进入 psql/pg_restore argv）。
if [ -z "${PGHOST:-}" ] && [ -n "${DATABASE_URL:-}" ]; then
    _url="${DATABASE_URL#*://}"
    _creds="${_url%%@*}"
    _hostpart="${_url#*@}"
    case "$_creds" in
        *:*) PGUSER="${PGUSER:-${_creds%%:*}}"; PGPASSWORD="${PGPASSWORD:-${_creds#*:}}" ;;
        *)   PGUSER="${PGUSER:-$_creds}" ;;
    esac
    case "$_hostpart" in
        */*) PGDATABASE="${PGDATABASE:-${_hostpart#*/}}" ;;
    esac
    _hostport="${_hostpart%%/*}"
    PGHOST="${PGHOST:-${_hostport%%:*}}"
    case "$_hostport" in
        *:*) PGPORT="${PGPORT:-${_hostport##*:}}" ;;
    esac
fi
: "${PGHOST:?需要 PGHOST/PGPORT/PGUSER/PGPASSWORD/PGDATABASE，或兼容的 DATABASE_URL}"
: "${PGDATABASE:?需要 PGDATABASE（或 DATABASE_URL 中的库名）}"
export PGHOST
export PGPORT="${PGPORT:-5432}"
export PGUSER
export PGPASSWORD="${PGPASSWORD:-}"
export PGDATABASE

if [ "${1:-}" != "" ]; then
    BACKUP_FILE="$1"
else
    BACKUP_FILE="$(ls -1t "$OUT_DIR"/deepresearch_*.dump 2>/dev/null | head -n 1 || true)"
fi
if [ -z "${BACKUP_FILE:-}" ] || [ ! -f "$BACKUP_FILE" ]; then
    echo "找不到备份文件（BACKUP_DIR=$OUT_DIR）" >&2
    exit 2
fi

psql -v ON_ERROR_STOP=1 -q -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
pg_restore --no-owner --no-privileges -d "$PGDATABASE" "$BACKUP_FILE"

migrations="$(psql -tAc "SELECT count(*) FROM schema_migrations" 2>/dev/null || echo 0)"
if [ "$migrations" -lt 1 ]; then
    echo "恢复后 schema_migrations 为空，恢复可能失败" >&2
    exit 1
fi
echo "restore ok: $BACKUP_FILE（migrations=$migrations）"
