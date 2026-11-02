#!/bin/sh
# L3 迁移执行器（P1 骨架）——由 docker-compose.staging.yml 的 migrate 服务调用，CI 也用它验证幂等。
#
# 约定：
# - 只前向执行 migrations/NNNN_slug.sql（字典序）；已执行的在 schema_migrations 中记录并跳过；
# - 每个迁移文件与其 `schema_migrations` 记录在**同一事务**内（psql --single-transaction +
#   stdin 依次喂「文件内容 + INSERT 记录」）：不存在「库已改但无记录」的半截状态（审计 #339）；
# - 记录文件内容 SHA-256：重跑时校验已应用文件未被篡改/改名，不一致直接失败；历史行
#   （sha256 IS NULL）下次重跑自动回填（审计 #339）；
# - 连接串不出现在 argv：DATABASE_URL 只在脚本内解析成 PG* 环境变量；口令进 psql argv
#   的风险与备份脚本同口径（审计 #339）；
# - 迁移文件内**不要**自己写 BEGIN/COMMIT（与 --single-transaction 冲突）；见 migrations/README.md。
set -eu

DATABASE_URL="${DATABASE_URL:?DATABASE_URL is required}"
MIGRATIONS_DIR="${MIGRATIONS_DIR:-/migrations}"

# schema_migrations 的 bootstrap 与 migrations/0031 保持一致：
# 0031 是规范的 DDL 记录；这里同样 ADD COLUMN IF NOT EXISTS，保证「先应用 0028–0030、
# 后应用 0031」的顺序下每一步都能写入指纹（幂等）。
SCHEMA_MIGRATIONS_BOOTSTRAP_SQL="
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    text PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE schema_migrations ADD COLUMN IF NOT EXISTS sha256 text;
"

# 审计 #339：URL 保留字符 percent-decode + 剥离 ?sslmode= 查询串（与 tools/backup.sh 同口径，
# 但本文件独立维护，不依赖 #326 的备份脚本改动）。口令中的 @ : / % 必须 URL 编码；
# 不支持 IPv6 字面量主机（生产用容器名/域名）。
_url_decode() {
    printf '%s' "$1" | sed \
        -e 's/%40/@/g' -e 's/%3A/:/g' -e 's/%3a/:/g' \
        -e 's/%2F/\//g' -e 's/%2f/\//g' -e 's/%25/%/g' \
        -e 's/%2B/+/g' -e 's/%20/ /g'
}
if [ -z "${PGHOST:-}" ] && [ -n "${DATABASE_URL:-}" ]; then
    _url="${DATABASE_URL#*://}"
    _creds="${_url%%@*}"
    _rest="${_url#*@}"
    case "$_creds" in
        *:*)
            PGUSER="${PGUSER:-$(_url_decode "${_creds%%:*}")}"
            PGPASSWORD="${PGPASSWORD:-$(_url_decode "${_creds#*:}")}"
            ;;
        *) PGUSER="${PGUSER:-$(_url_decode "$_creds")}" ;;
    esac
    case "$_rest" in
        */*)
            _hostport="${_rest%%/*}"
            _dbpart="$(_url_decode "${_rest#*/}")"
            PGDATABASE="${PGDATABASE:-${_dbpart%%\?*}}"
            ;;
        *) _hostport="$_rest" ;;
    esac
    PGHOST="${PGHOST:-$(_url_decode "${_hostport%%:*}")}"
    case "$_hostport" in
        *:*) PGPORT="${PGPORT:-$(_url_decode "${_hostport##*:}")}" ;;
    esac
fi
: "${PGHOST:?需要 PGHOST/PGPORT/PGUSER/PGPASSWORD/PGDATABASE，或兼容的 DATABASE_URL}"
: "${PGDATABASE:?需要 PGDATABASE（或 DATABASE_URL 中的库名）}"
export PGHOST
export PGPORT="${PGPORT:-5432}"
export PGUSER
export PGPASSWORD="${PGPASSWORD:-}"
export PGDATABASE

hash_file() {
    if command -v sha256sum > /dev/null 2>&1; then
        sha256sum "$1" | awk '{print $1}'
    elif command -v openssl > /dev/null 2>&1; then
        openssl dgst -sha256 "$1" | awk '{print $NF}'
    else
        echo "FAIL: 需要 sha256sum 或 openssl 计算迁移文件 hash" >&2
        exit 1
    fi
}

psql -v ON_ERROR_STOP=1 -q -c "$SCHEMA_MIGRATIONS_BOOTSTRAP_SQL"

applied_count=0
for file in "$MIGRATIONS_DIR"/*.sql; do
    [ -e "$file" ] || continue
    version="$(basename "$file" .sql)"
    checksum="$(hash_file "$file")"
    row="$(psql -v ON_ERROR_STOP=1 -tA -v version="$version" \
        -c "SELECT 'row:' || COALESCE(sha256, '') FROM schema_migrations WHERE version = :'version'")"
    if [ "${row#row:}" != "$row" ]; then
        stored="${row#row:}"
        if [ -z "$stored" ]; then
            # 历史行（指纹引入前应用）：回填当前文件 hash，后续重跑按 hash 校验
            echo "backfill: $version sha256=$checksum"
            psql -v ON_ERROR_STOP=1 -q -v version="$version" -v checksum="$checksum" \
                -c "UPDATE schema_migrations SET sha256 = :'checksum' WHERE version = :'version'"
        elif [ "$stored" != "$checksum" ]; then
            echo "FAIL: 已应用迁移文件内容与记录不一致：$version（stored=$stored file=$checksum）" >&2
            echo "只前向铁律：已应用迁移文件不得修改；如确需修复，另写前向迁移并人工裁决记录。" >&2
            exit 1
        fi
        echo "skip: $version"
        continue
    fi
    echo "apply: $version（sha256=$checksum）"
    # 迁移内容 + 记录写入同一事务（psql --single-transaction 包住 stdin 的全部语句）
    if ! {
        cat "$file"
        printf "\nINSERT INTO schema_migrations (version, sha256) VALUES (:'version', :'checksum');\n"
    } | psql -v ON_ERROR_STOP=1 -q --single-transaction \
            -v version="$version" -v checksum="$checksum" -f -; then
        echo "FAIL: 迁移失败（同一事务整体回滚，无半截）：$version" >&2
        exit 1
    fi
    applied_count=$((applied_count + 1))
done

echo "migrations done (applied=$applied_count)"
