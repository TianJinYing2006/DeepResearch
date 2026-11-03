#!/bin/sh
# 任务库恢复（P8-A）：清空 public schema 后从备份恢复，并校验迁移记录可读。
#
# 用法（宿主机或运行 PG 的容器内）：
#   PGHOST=... PGPORT=... PGUSER=... PGPASSWORD=... PGDATABASE=... tools/restore.sh [<dump 文件>]
#   兼容：DATABASE_URL=postgresql://...（脚本内解析，不传给子进程 argv；口令特殊字符需 URL 编码）
#   缺省取 BACKUP_DIR（默认 /backups）里最新的产物；若最新是加密备份（.dump.pem / .dump.enc），
#   脚本会**明确报错并给出解密指引** —— 先解密成明文 .dump 再传入（审计 #339）。
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
    # 审计 #339：默认要能看见生产加密备份，否则「什么都不做/报找不到」会误导运维；
    # 选中加密产物时下面给出明确解密指引（不静默）。
    BACKUP_FILE="$(ls -1t "$OUT_DIR"/deepresearch_*.dump \
        "$OUT_DIR"/deepresearch_*.dump.pem "$OUT_DIR"/deepresearch_*.dump.enc \
        2>/dev/null | head -n 1 || true)"
fi
if [ -z "${BACKUP_FILE:-}" ] || [ ! -f "$BACKUP_FILE" ]; then
    echo "找不到备份文件（BACKUP_DIR=$OUT_DIR）" >&2
    exit 2
fi

case "$BACKUP_FILE" in
    *.dump.enc)
        echo "FAIL: $BACKUP_FILE 是对称加密备份，恢复前需先解密为明文 .dump：" >&2
        echo "  openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -pass env:BACKUP_PASSPHRASE \\" >&2
        echo "    -in \"$BACKUP_FILE\" -out /tmp/restore.dump" >&2
        echo "  然后：tools/restore.sh /tmp/restore.dump（解密与密钥见 runbook §2.3/§10）" >&2
        exit 2
        ;;
    *.dump.pem)
        echo "FAIL: $BACKUP_FILE 是非对称加密备份，恢复前需先用离线私钥解密为明文 .dump：" >&2
        echo "  openssl smime -decrypt -in \"$BACKUP_FILE\" -inkey <离线私钥> -out /tmp/restore.dump" >&2
        echo "  然后：tools/restore.sh /tmp/restore.dump（私钥必须离线保管，见 runbook §2.3/§10）" >&2
        exit 2
        ;;
esac

psql -v ON_ERROR_STOP=1 -q -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
pg_restore --no-owner --no-privileges -d "$PGDATABASE" "$BACKUP_FILE"

migrations="$(psql -tAc "SELECT count(*) FROM schema_migrations" 2>/dev/null || echo 0)"
if [ "$migrations" -lt 1 ]; then
    echo "恢复后 schema_migrations 为空，恢复可能失败" >&2
    exit 1
fi
echo "restore ok: $BACKUP_FILE（migrations=$migrations）"
