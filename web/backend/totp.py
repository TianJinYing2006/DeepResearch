"""TOTP（RFC 6238）/ HOTP（RFC 4226）与恢复码原语（需求 20 P1-3）。

- 只用标准库（`hmac` / `base64` / `struct` / `secrets`）实现动态截断，**不引入 pyotp**；
- TOTP secret 的**加密存储**在 `secretbox.py`（本模块只处理明文与派生）；
- 恢复码只在此生成明文，**持久化侧只存 SHA-256 摘要**（`hash_recovery_code`）。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
from urllib.parse import quote

#: RFC 4226 §4 推荐密钥长度（160 bit）
SECRET_BYTES = 20
DEFAULT_STEP_SECONDS = 30
DEFAULT_DIGITS = 6
#: 允许的时钟漂移（±1 步 = ±30s）
DEFAULT_WINDOW = 1

RECOVERY_CODE_COUNT = 10
#: 5 字节 → base32 8 字符（展示为 `XXXX-XXXX`）
RECOVERY_CODE_BYTES = 5


def new_totp_secret() -> bytes:
    return secrets.token_bytes(SECRET_BYTES)


def encode_secret(secret: bytes) -> str:
    """base32（去填充，大写）—— 供 otpauth URI 与手动录入。"""
    return base64.b32encode(secret).decode("ascii").rstrip("=")


def decode_secret(encoded: str) -> bytes:
    padded = (encoded or "").strip().upper()
    padded += "=" * ((8 - len(padded) % 8) % 8)
    return base64.b32decode(padded)


def _hotp(secret: bytes, counter: int, digits: int = DEFAULT_DIGITS) -> str:
    digest = hmac.new(secret, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(code % (10 ** digits)).zfill(digits)


def totp_at(secret: bytes, at: float, *, step: int = DEFAULT_STEP_SECONDS,
            digits: int = DEFAULT_DIGITS) -> str:
    return _hotp(secret, int(at // step), digits)


def verify_totp(secret: bytes, code: str, at: float, *, step: int = DEFAULT_STEP_SECONDS,
                digits: int = DEFAULT_DIGITS, window: int = DEFAULT_WINDOW) -> bool:
    """校验 TOTP（接受 ±`window` 个步长）；**常量时间比较**，格式不符即 False。"""
    candidate = (code or "").strip().replace(" ", "")
    if not candidate.isdigit() or len(candidate) != digits:
        return False
    counter = int(at // step)
    for delta in range(-window, window + 1):
        if hmac.compare_digest(_hotp(secret, counter + delta, digits), candidate):
            return True
    return False


def provisioning_uri(encoded_secret: str, *, account: str, issuer: str) -> str:
    """`otpauth://` 供给 URI（认证器 App 扫码或手动录入）。"""
    label = quote(f"{issuer}:{account}", safe="")
    return (f"otpauth://totp/{label}?secret={encoded_secret}"
            f"&issuer={quote(issuer, safe='')}&algorithm=SHA1"
            f"&digits={DEFAULT_DIGITS}&period={DEFAULT_STEP_SECONDS}")


def new_recovery_codes(count: int = RECOVERY_CODE_COUNT) -> list[str]:
    """生成一次性恢复码明文（仅在生成响应中出现一次；持久化只存摘要）。"""
    codes = []
    for _ in range(count):
        raw = base64.b32encode(secrets.token_bytes(RECOVERY_CODE_BYTES)).decode("ascii")
        codes.append(f"{raw[:4]}-{raw[4:8]}")
    return codes


def normalize_recovery_code(code: str) -> str:
    return (code or "").strip().upper().replace("-", "").replace(" ", "")


def hash_recovery_code(code: str) -> str:
    """恢复码摘要（高熵 ⇒ 直接 SHA-256，无需慢哈希）。"""
    return hashlib.sha256(normalize_recovery_code(code).encode("ascii")).hexdigest()
