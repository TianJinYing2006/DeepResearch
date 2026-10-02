#!/bin/sh
# 任务库备份（P8-A / 需求 20 数据安全）：pg_dump 自定义格式 + 可读性校验 + 保留最近 N 份。
#
# 用法（宿主机或运行 PG 的容器内）：
#   DATABASE_URL=postgresql://... BACKUP_DIR=/backups tools/backup.sh
#
# 设计口径（与上线清单 §3.8 一致）：
# - **“备份成功”以 `pg_restore -l` 可列出目录为准** —— 只写文件不校验等于没有备份；
# - 保留最近 BACKUP_KEEP 份（默认 14），旧备份自动清理；
# - CI 的 infra job 用同一脚本做恢复演练（备份 → 清库 → 恢复 → 结构断言）。
#
# 可选加密（需求 20 §8 数据安全）：
#   BACKUP_ENCRYPT=1 BACKUP_PASSPHRASE=... tools/backup.sh
#   - AES-256-CBC + PBKDF2（200k 迭代）；口令只从环境变量读取（不进命令行历史 / 日志）；
#   - 加密件 `deepresearch_<stamp>.dump.enc`：先解密到临时文件做 pg_restore -l 校验，通过后删除明文；
#   - 恢复：openssl enc -d ... | pg_restore（见 docs/operations/release-runbook.md §2.3）；
#   - 未设 BACKUP_ENCRYPT 时行为与历史完全一致（CI 演练不受影响）。
set -eu

DATABASE_URL="${DATABASE_URL:?需要 DATABASE_URL}"
OUT_DIR="${BACKUP_DIR:-/backups}"
KEEP="${BACKUP_KEEP:-14}"

mkdir -p "$OUT_DIR"
stamp="$(date +%Y%m%d_%H%M%S)"
plain="$OUT_DIR/deepresearch_$stamp.dump"

pg_dump -Fc "$DATABASE_URL" -f "$plain"

if [ "${BACKUP_ENCRYPT:-0}" = "1" ]; then
    : "${BACKUP_PASSPHRASE:?BACKUP_ENCRYPT=1 时需要 BACKUP_PASSPHRASE（仅从环境变量注入）}"
    enc="$plain.enc"
    openssl enc -aes-256-cbc -salt -pbkdf2 -iter 200000 \
        -pass env:BACKUP_PASSPHRASE -in "$plain" -out "$enc"
    verify="$OUT_DIR/.verify.$$.dump"
    openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 \
        -pass env:BACKUP_PASSPHRASE -in "$enc" -out "$verify"
    pg_restore -l "$verify" > /dev/null
    rm -f "$verify" "$plain"
    final="$enc"
else
    pg_restore -l "$plain" > /dev/null
    final="$plain"
fi

if [ "$KEEP" -gt 0 ]; then
    old_list="$OUT_DIR/.to_delete.$$"
    ls -1t "$OUT_DIR"/deepresearch_*.dump "$OUT_DIR"/deepresearch_*.dump.enc 2>/dev/null \
        | tail -n +$((KEEP + 1)) > "$old_list" || true
    while IFS= read -r old; do
        [ -n "$old" ] && rm -f "$old"
    done < "$old_list"
    rm -f "$old_list"
fi

echo "backup ok: $final"
