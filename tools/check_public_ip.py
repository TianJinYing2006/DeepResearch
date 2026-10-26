#!/usr/bin/env python3
"""公开仓脱敏守卫：禁止 tracked 文本出现真实公网 IPv4。

背景（2026-10-26 评审 C0-2）：staging 真实 IP 曾写进部署 workflow、runbook、
project-status 与 requirements。公开仓只允许占位符；真实部署实参放本地
`local-artifacts/` / `.env` / `.workbuddy/`（均已被 gitignore）。

口径：
- 只扫 `git ls-files` 能看到的文本文件；二进制与依赖 lock 跳过（版本号会误报）。
- 放行：回环 / 私网 / 链路本地 / 未指定 / 多播 / 保留网段；RFC 5737 文档网段。
- 放行：测试夹具白名单（`FIXTURE_ALLOWLIST`，逐条写理由）。
- 基线：`BASELINE_PATHS` 中仍含真实 host 的部署文件只输出 WARN（PR-B 迁移到
  GitHub Secrets 后必须删掉基线，否则守卫会提醒基线过期）。

用法：`python tools/check_public_ip.py [--root <repo>] [--strict]`
退出码：0 = 无基线外公网 IP；1 = 发现新泄漏（或 --strict 下基线仍有命中）。
"""

from __future__ import annotations

import argparse
import ipaddress
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

IPV4_RE = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")

DOC_NETWORKS = tuple(
    ipaddress.ip_network(net) for net in ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24")
)

# 测试夹具/非 IP 字面量：逐条给出理由；禁止无理由扩容。
FIXTURE_ALLOWLIST = {
    "1.1.1.1": "e2e 测试夹具（CDN/DNS 字面量）",
    "2.2.2.2": "e2e 测试夹具（拒绝路径）",
    "9.9.9.9": "单测夹具（登录通知）",
    "1.2.3.4": "单测夹具（通用示例）",
    "1.2.3.99": "单测夹具（登录通知）",
    "93.184.216.34": "单测夹具（example.com 解析地址）",
    "3.1.1.2": "NIST SP 800-63B-4 §3.1.1.2 版本号，非 IP",
}

# 已知存量：部署链路仍使用真实 host；PR-B 迁移到 secrets 后必须清空本基线。
BASELINE_PATHS = {
    ".github/workflows/deploy-staging.yml": "PR-B：迁移到 GitHub Secrets 后移除",
    "tools/deploy.sh": "PR-B：迁移到环境变量后移除",
}

SKIP_SUFFIXES = (".lock",)
SKIP_NAME_MARKERS = ("-lock.", "package-lock.json")


def classify_ip(raw: str) -> str:
    """返回 public / private / doc / allowlisted / invalid。"""
    try:
        addr = ipaddress.ip_address(raw)
    except ValueError:
        return "invalid"
    if any(addr in net for net in DOC_NETWORKS):
        return "doc"
    if (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_unspecified
        or addr.is_multicast
        or addr.is_reserved
    ):
        return "private"
    if raw in FIXTURE_ALLOWLIST:
        return "allowlisted"
    return "public"


def find_public_ips(text: str) -> list[tuple[int, str]]:
    """返回文本中未放行的公网 IPv4：[(行号, ip)]。"""
    hits: list[tuple[int, str]] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        for match in IPV4_RE.finditer(line):
            ip = match.group(0)
            if classify_ip(ip) == "public":
                hits.append((lineno, ip))
    return hits


def should_skip(rel_path: str) -> bool:
    name = Path(rel_path).name
    return rel_path.endswith(SKIP_SUFFIXES) or any(marker in name for marker in SKIP_NAME_MARKERS)


def is_text_file(data: bytes) -> bool:
    if b"\x00" in data[:2048]:
        return False
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def tracked_files(root: Path) -> list[str]:
    proc = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"],
        check=True,
        capture_output=True,
    )
    return [part for part in proc.stdout.decode("utf-8", "replace").split("\0") if part]


def scan(root: Path) -> tuple[list[tuple[str, int, str]], list[tuple[str, int, str]]]:
    """扫描 tracked 文本；返回 (errors, baseline_warnings)。"""
    errors: list[tuple[str, int, str]] = []
    warnings: list[tuple[str, int, str]] = []
    for rel_path in tracked_files(root):
        if should_skip(rel_path):
            continue
        try:
            data = (root / rel_path).read_bytes()
        except OSError:
            continue
        if not is_text_file(data):
            continue
        hits = find_public_ips(data.decode("utf-8"))
        if not hits:
            continue
        target = warnings if rel_path in BASELINE_PATHS else errors
        for lineno, ip in hits:
            target.append((rel_path, lineno, ip))
    return errors, warnings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="公开仓脱敏守卫：检测 tracked 文本中的真实公网 IPv4")
    parser.add_argument("--root", type=Path, default=REPO_ROOT, help="仓库根目录（默认脚本上一级）")
    parser.add_argument("--strict", action="store_true", help="基线命中同样视为失败（基线清空后应使用）")
    args = parser.parse_args(argv)

    errors, warnings = scan(args.root)
    for rel_path, lineno, ip in warnings:
        reason = BASELINE_PATHS.get(rel_path, "基线")
        print(f"WARN 基线 {rel_path}:{lineno} 含 {ip}（{reason}）")
    baseline_hit_paths = {rel_path for rel_path, _lineno, _ip in warnings}
    for rel_path, reason in BASELINE_PATHS.items():
        if rel_path not in baseline_hit_paths:
            print(f"WARN 基线过期 {rel_path}（{reason}）：已无公网 IP 命中，请移除该基线")
    for rel_path, lineno, ip in errors:
        print(f"ERROR 公网 IP {ip} 出现在 {rel_path}:{lineno}（请改为占位符或本地配置）")

    failed = bool(errors) or (args.strict and bool(warnings))
    print(
        f"公开仓脱敏守卫：{len(errors)} error / {len(warnings)} baseline warning"
        f"{'（strict）' if args.strict else ''}"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
