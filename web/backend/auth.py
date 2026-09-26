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

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

SESSION_COOKIE = "dr_session"
CSRF_COOKIE = "dr_csrf"
CSRF_HEADER = "X-CSRF-Token"

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_hasher = PasswordHasher()

MIN_PASSWORD_LENGTH = 10


def normalize_email(email: str) -> str:
    return email.strip().lower()


def is_valid_email(email: str) -> bool:
    return bool(_EMAIL_RE.match(email))


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


def new_invite_code() -> str:
    return secrets.token_urlsafe(16)
