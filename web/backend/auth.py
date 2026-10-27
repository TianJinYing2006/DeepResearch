"""账号与会话原语（P4-A）。

- 密码：Argon2id（`argon2-cffi` 默认参数）；
- 会话令牌 / 邀请码：`secrets.token_urlsafe`；**数据库只存 SHA-256 摘要**；
- CSRF：双提交 Cookie（`dr_csrf` 非 httpOnly + 请求头 `X-CSRF-Token`）。

这里只放纯函数与常量；持久化在 `store.RunStore`，HTTP 组装在 `main.py`。
"""
from __future__ import annotations

import hashlib
import re
import secrets
from typing import Iterable, Optional

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

SESSION_COOKIE = "dr_session"
CSRF_COOKIE = "dr_csrf"
CSRF_HEADER = "X-CSRF-Token"

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_hasher = PasswordHasher()

MIN_PASSWORD_LENGTH = 12


def host_prefix_cookie_name(name: str, secure: bool) -> str:
    """RFC 6265bis `__Host-` 前缀：要求 Secure + Path=/ + 无 Domain。

    HTTP 本地调试（`DR_COOKIE_SECURE=false`）时浏览器会拒收非 Secure 的 `__Host-`
    Cookie，因此仅在 HTTPS 模式下启用前缀，其余场景退回裸名（测试/本地行为不变）。
    """
    return f"__Host-{name}" if secure else name


#: 高频弱口令（NIST SP 800-63B-4 §3.1.1.2：比对常用/已泄露口令；**精确匹配**，不误伤长口令）
BLOCKED_PASSWORDS = frozenset({
    "password", "password1", "password123", "password1234", "passw0rd", "p@ssw0rd",
    "123456", "1234567", "12345678", "123456789", "1234567890", "123123123",
    "qwerty", "qwerty123", "qwertyuiop", "1q2w3e4r", "1qaz2wsx",
    "abc123", "abcd1234", "a1b2c3d4",
    "111111", "000000", "888888", "666666", "999999",
    "iloveyou", "admin", "admin123", "administrator", "root", "letmein",
    "welcome", "welcome1", "monkey", "dragon", "master", "shadow",
    "sunshine", "princess", "football", "baseball", "superman", "trustno1",
    "whatever", "changeme", "secret", "deepresearch", "ikaoju",
})


def check_password_strength(password: str, *, email: str = "") -> Optional[str]:
    """NIST SP 800-63B-4 口径的轻量口令检查；合规返回 ``None``，否则返回错误文案。

    - 长度与上限由调用方（Pydantic ``min_length``）承担；
    - 精确比对常用弱口令（去首尾空白 + 小写，不误伤长口令）；
    - 重复字符检测（去重后 < 4）；
    - 上下文词不得出现在口令中（服务名 / 邮箱本地部分）。
    """
    normalized = (password or "").strip().lower()
    if normalized in BLOCKED_PASSWORDS:
        return "该密码过于常见，请更换为更安全的口令"
    if len(set(normalized)) < 4:
        return "密码过于简单（重复字符过多）"
    if "deepresearch" in normalized or "ikaoju" in normalized:
        return "密码不能包含服务名称"
    local = (email.split("@", 1)[0] if email else "").strip().lower()
    if len(local) >= 4 and local in normalized:
        return "密码不能包含邮箱账号名"
    return None


def normalize_email(email: str) -> str:
    return email.strip().lower()


def is_valid_email(email: str) -> bool:
    return bool(_EMAIL_RE.match(email))


def _coarse_network(ip: Optional[str]) -> str:
    """把客户端地址收敛成「网段」标识，降低动态 IP 造成的「新设备」误判。

    - IPv4：取前三段（/24）；
    - IPv6：取前四组（/64）；
    - 其他 / 空：原样（截断）。
    """
    value = (ip or "").strip()
    if not value:
        return "unknown"
    if ":" in value:  # IPv6
        return ":".join(value.split(":")[:4])[:64] or "unknown"
    parts = value.split(".")
    if len(parts) == 4:
        return ".".join(parts[:3])
    return value[:64]


def device_key(user_agent: Optional[str], ip: Optional[str]) -> str:
    """会话「设备」标识：`(UA, IP 网段)` 的粗粒度指纹。

    ⚠️ 仅用于「新设备登录提醒」这一提示信号；不做跨站追踪（见需求 20 §7.8）。
    """
    ua = (user_agent or "").strip().lower()[:200]
    return f"{ua}|{_coarse_network(ip)}"


def is_new_device(prior_devices: Iterable[str], user_agent: Optional[str],
                  ip: Optional[str]) -> bool:
    """当前设备对用户而言是否「新设备」（需求 20 P1-1b）。

    务实口径：仅当该用户**已有活跃会话**（`prior_devices` 非空）且无一与当前
    `(UA, IP 网段)` 匹配时才算新设备 —— 首次登录（无历史，如注册后首次）不发通知，
    避免「注册即打扰」。

    历史来源为**活跃会话表**（登出 / 过期即消失）⇒ 只在「已知且在线」的维度上保守：
    对很久以前登出的自家设备可能误报一次，但不会漏报陌生设备。
    """
    if not prior_devices:
        return False
    return device_key(user_agent, ip) not in set(prior_devices)


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
