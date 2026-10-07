#!/bin/sh
# 备份校验：最新备份的「新鲜度 + 可读性」（需求 1，2026-10-07；与 tools/backup.sh 同口径：
# 「备份成功」以 `pg_restore -l` 可列出目录为准 —— 只写文件不校验等于没有备份）。
#
# 用法：
#   BACKUP_DIR=/opt/deepresearch/backups tools/backup-verify.sh            # 校验最新一份
#   tools/backup-verify.sh /path/to/deepresearch_20261007_033000.dump      # 校验指定文件
#   BACKUP_PASSPHRASE=... tools/backup-verify.sh                           # 对称加密产物（.dump.enc）
#   BACKUP_RECIPIENT_KEY=... tools/backup-verify.sh                        # 非对称产物（.dump.pem，离线私钥）
#
# 校验内容：
#   1. 存在且非空的备份文件（默认取 BACKUP_DIR 下 mtime 最新的一份）；
#   2. 新鲜度：mtime 不超过 MAX_AGE_HOURS（默认 26 —— 容忍每日 cron 加一次延迟）；
#   3. 可读性：`pg_restore -l` 能列出目录。
#      - 明文 .dump：直接校验；
#      - .enc / .pem：**需提供口令 / 私钥**；未提供时只做结构校验并显式输出
#        `structure-only`（可解性由「带离线私钥的恢复演练」保证，见 release-runbook §10）。
#
# 退出码：0 通过（含 structure-only）；2 找不到备份；3 空文件；4 过期；5 解密失败；6 不可读。
set -eu

OUT_DIR="${BACKUP_DIR:-/backups}"
MAX_AGE_HOURS="${MAX_AGE_HOURS:-26}"
TARGET="${1:-}"

if [ -z "$TARGET" ]; then
    TARGET="$(ls -1t "$OUT_DIR"/deepresearch_*.dump "$OUT_DIR"/deepresearch_*.dump.enc \
        "$OUT_DIR"/deepresearch_*.dump.pem 2>/dev/null | head -n 1 || true)"
fi
if [ -z "$TARGET" ] || [ ! -f "$TARGET" ]; then
    echo "FAIL: 找不到备份文件（BACKUP_DIR=$OUT_DIR）" >&2
    exit 2
fi
if [ ! -s "$TARGET" ]; then
    echo "FAIL: 备份文件为空：$TARGET" >&2
    exit 3
fi

# 新鲜度：find -mmin +N 命中即视为过期（busybox/GNU find 通用）
if [ -n "$(find "$TARGET" -mmin +$((MAX_AGE_HOURS * 60)) 2>/dev/null || true)" ]; then
    echo "FAIL: 备份超过 ${MAX_AGE_HOURS}h 未更新：$TARGET" >&2
    exit 4
fi

TMP="$(mktemp "${TMPDIR:-/tmp}/dr-verify.XXXXXX")"
trap 'rm -f "$TMP"' EXIT INT TERM

MODE="plain"
case "$TARGET" in
    *.dump)
        cp "$TARGET" "$TMP"
        ;;
    *.dump.enc)
        MODE="structure-only"
        if [ -n "${BACKUP_PASSPHRASE:-}" ] && ! command -v openssl >/dev/null 2>&1; then
            echo "FAIL: 需要 openssl 才能解密 .enc（当前镜像/环境未安装；可改用 postgres:16 或宿主机执行）" >&2
            exit 6
        fi
        if [ -n "${BACKUP_PASSPHRASE:-}" ]; then
            MODE="decrypted"
            openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 \
                -pass env:BACKUP_PASSPHRASE -in "$TARGET" -out "$TMP" \
                || { echo "FAIL: 解密失败（对称 .enc）: $TARGET" >&2; exit 5; }
        fi
        ;;
    *.dump.pem)
        MODE="structure-only"
        if [ -n "${BACKUP_RECIPIENT_KEY:-}" ] && ! command -v openssl >/dev/null 2>&1; then
            echo "FAIL: 需要 openssl 才能解密 .pem（当前镜像/环境未安装；可改用 postgres:16 或宿主机执行）" >&2
            exit 6
        fi
        if [ -n "${BACKUP_RECIPIENT_KEY:-}" ]; then
            MODE="decrypted"
            openssl smime -decrypt -in "$TARGET" -inkey "$BACKUP_RECIPIENT_KEY" -out "$TMP" \
                || { echo "FAIL: 解密失败（非对称 .pem）: $TARGET" >&2; exit 5; }
        fi
        ;;
    *)
        echo "FAIL: 未知产物类型（期望 .dump / .dump.enc / .dump.pem）：$TARGET" >&2
        exit 6
        ;;
esac

if [ ! -s "$TMP" ]; then
    echo "verify structure-only ok: $TARGET（加密产物未提供口令/私钥；可解性请在恢复演练中用离线密钥验证）"
    exit 0
fi

if ! pg_restore -l "$TMP" > /tmp/dr-verify.list.$$ 2>/dev/null; then
    rm -f /tmp/dr-verify.list.$$
    echo "FAIL: pg_restore -l 不可读：$TARGET" >&2
    exit 6
fi
ENTRIES="$(wc -l < /tmp/dr-verify.list.$$ | tr -d ' ')"
rm -f /tmp/dr-verify.list.$$
echo "verify ok: $TARGET（mode=$MODE，pg_restore -l 可读，目录条目 ${ENTRIES} 行）"
