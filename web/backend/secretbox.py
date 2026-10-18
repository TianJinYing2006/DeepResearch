"""TOTP secret 的**可逆加密**（需求 20 P1-3；设计 §7.8 / §8.3）。

- 算法：**AES-256-GCM**（`cryptography` 标准件；不自造加密原语）；
  密文格式 `nonce(12B) || ciphertext+tag`，AAD 固定 `b"dr-mfa"`；
- 密钥：环境变量 `DR_MFA_SECRET_KEY`（base64 编码的 32 字节；生成见 `generate_key_b64()`）；
- **fail-closed**：密钥未配置 / 非法 ⇒ `configured()` 为 False ⇒ MFA 端点 503，
  且任何既有因子一律不放行（防止「删掉密钥就绕过第二步」的降级攻击）；
- 密钥轮换：本版不支持多密钥解封——轮换需先关闭 MFA 再重新录入（已登记在需求 20 §6）。
"""
from __future__ import annotations

import base64
import os
import secrets
from typing import Optional

NONCE_BYTES = 12
_AAD = b"dr-mfa"
KEY_BYTES = 32


def _raw_key() -> Optional[bytes]:
    value = (os.getenv("DR_MFA_SECRET_KEY") or "").strip()
    if not value:
        return None
    try:
        key = base64.b64decode(value, validate=True)
    except Exception:  # noqa: BLE001 —— 非法配置按「未配置」处理（fail-closed）
        return None
    return key if len(key) == KEY_BYTES else None


def configured() -> bool:
    return _raw_key() is not None


def _require_key() -> bytes:
    key = _raw_key()
    if key is None:
        raise RuntimeError("DR_MFA_SECRET_KEY 未配置或非法（需 base64 的 32 字节）")
    return key


def encrypt(plaintext: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    nonce = secrets.token_bytes(NONCE_BYTES)
    return nonce + AESGCM(_require_key()).encrypt(nonce, plaintext, _AAD)


def decrypt(blob: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    nonce, ciphertext = blob[:NONCE_BYTES], blob[NONCE_BYTES:]
    return AESGCM(_require_key()).decrypt(nonce, ciphertext, _AAD)


def generate_key_b64() -> str:
    """生成新密钥（运维初始化用）。"""
    return base64.b64encode(secrets.token_bytes(KEY_BYTES)).decode("ascii")
