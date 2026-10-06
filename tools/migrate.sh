#!/bin/sh
# L3 迁移执行器（P1 骨架）——由 docker-compose.staging.yml 的 migrate 服务调用，CI 也用它验证幂等。
#
# 约定：
# - 只前向执行 migrations/NNNN_slug.sql（字典序）；已执行的在 schema_migrations 中记录并跳过；
# - 每个文件在单事务内执行（--single-transaction），失败不留下半截迁移；
# - 迁移文件内**不要**自己写 BEGIN/COMMIT（与 --single-transaction 冲突）；见 migrations/README.md。
set -eu

DATABASE_URL="${DATABASE_URL:?DATABASE_URL is required}"
MIGRATIONS_DIR="${MIGRATIONS_DIR:-/migrations}"

psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -q -c "
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    text PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
);"

applied_count=0
for file in "$MIGRATIONS_DIR"/*.sql; do
    [ -e "$file" ] || continue
    version="$(basename "$file" .sql)"
    if [ "$(psql "$DATABASE_URL" -tA -c "SELECT 1 FROM schema_migrations WHERE version = '$version'")" = "1" ]; then
        echo "skip: $version"
        continue
    fi
    echo "apply: $version"
    psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -q --single-transaction -f "$file"
    psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -q -c "INSERT INTO schema_migrations(version) VALUES ('$version')"
    applied_count=$((applied_count + 1))
done

echo "migrations done (applied=$applied_count)"
