#!/bin/sh
# 任务库备份（P8-A / 需求 20 §8 数据安全）：pg_dump 自定义格式 + 可读性校验 + 保留最近 N 份。
#
# 用法（宿主机或运行 PG 的容器内）：
#   DATABASE_URL=postgresql://... BACKUP_DIR=/backups tools/backup.sh
#
# 设计口径（与上线清单 §3.8 一致）：
# - **“备份成功”以 `pg_restore -l` 可列出目录为准** —— 只写文件不校验等于没有备份；
# - 保留最近 BACKUP_KEEP 份（默认 14），旧备份自动清理；
# - CI 的 infra job 用同一脚本做恢复演练（备份 → 清库 → 恢复 → 结构断言）。
#
# 加密模式（需求 20 §8；二选一，优先非对称）：
#   1) 非对称（推荐）：BACKUP_RECIPIENT_CERT=/path/recipient.crt
#      - 服务器只存**公钥证书**；私钥离线保管（密码管理器/离线盘）→ 服务器被攻陷也解不开历史备份；
#      - `openssl smime -encrypt -aes256`；产物 `deepresearch_*.dump.pem`；
#      - 校验在加密前对明文完成；密文可解性由定期恢复演练（离线私钥）验证。
#   2) 对称（备选）：BACKUP_ENCRYPT=1 + BACKUP_PASSPHRASE（仅环境变量注入）
#      - AES-256-CBC + PBKDF2(200k)；产物 `deepresearch_*.dump.enc`；解密回验后删除明文。
#   - 都不设时行为与历史完全一致（CI 演练不受影响）。
set -eu

DATABASE_URL="${DATABASE_URL:?需要 DATABASE_URL}"
OUT_DIR="${BACKUP_DIR:-/backups}"
KEEP="${BACKUP_KEEP:-14}"

mkdir -p "$OUT_DIR"
stamp="$(date +%Y%m%d_%H%M%S)"
plain="$OUT_DIR/deepresearch_$stamp.dump"

pg_dump -Fc "$DATABASE_URL" -f "$plain"
# 校验口径不降级：无论后续是否加密，先对明文做 pg_restore -l
pg_restore -l "$plain" > /dev/null

if [ -n "${BACKUP_RECIPIENT_CERT:-}" ]; then
    enc="$plain.pem"
    openssl smime -encrypt -aes256 -binary -in "$plain" -out "$enc" "$BACKUP_RECIPIENT_CERT"
    rm -f "$plain"
    final="$enc"
elif [ "${BACKUP_ENCRYPT:-0}" = "1" ]; then
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
    final="$plain"
fi

if [ "$KEEP" -gt 0 ]; then
    old_list="$OUT_DIR/.to_delete.$$"
    ls -1t "$OUT_DIR"/deepresearch_*.dump "$OUT_DIR"/deepresearch_*.dump.enc \
        "$OUT_DIR"/deepresearch_*.dump.pem 2>/dev/null \
        | tail -n +$((KEEP + 1)) > "$old_list" || true
    while IFS= read -r old; do
        [ -n "$old" ] && rm -f "$old"
    done < "$old_list"
    rm -f "$old_list"
fi

echo "backup ok: $final"
