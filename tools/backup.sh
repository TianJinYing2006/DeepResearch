#!/bin/sh
# 任务库备份（P8-A）：pg_dump 自定义格式 + 可读性校验 + 保留最近 N 份。
#
# 用法（宿主机或运行 PG 的容器内）：
#   DATABASE_URL=postgresql://... BACKUP_DIR=/backups tools/backup.sh
#
# 设计口径（与上线清单 §3.8 一致）：
# - **“备份成功”以 `pg_restore -l` 可列出目录为准** —— 只写文件不校验等于没有备份；
# - 保留最近 BACKUP_KEEP 份（默认 14），旧备份自动清理；
# - CI 的 infra job 用同一脚本做恢复演练（备份 → 清库 → 恢复 → 结构断言）。
set -eu

DATABASE_URL="${DATABASE_URL:?需要 DATABASE_URL}"
OUT_DIR="${BACKUP_DIR:-/backups}"
KEEP="${BACKUP_KEEP:-14}"

mkdir -p "$OUT_DIR"
stamp="$(date +%Y%m%d_%H%M%S)"
file="$OUT_DIR/deepresearch_$stamp.dump"

pg_dump -Fc "$DATABASE_URL" -f "$file"
pg_restore -l "$file" > /dev/null

if [ "$KEEP" -gt 0 ]; then
    old_list="$OUT_DIR/.to_delete.$$"
    ls -1t "$OUT_DIR"/deepresearch_*.dump 2>/dev/null | tail -n +$((KEEP + 1)) > "$old_list" || true
    while IFS= read -r old; do
        [ -n "$old" ] && rm -f "$old"
    done < "$old_list"
    rm -f "$old_list"
fi

echo "backup ok: $file"
