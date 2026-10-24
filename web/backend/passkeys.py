"""需求 20 P1-2：Passkey（WebAuthn）服务端选项与签名校验。

边界：本模块只做 WebAuthn 协议层（选项生成 / 验证），不碰会话与数据库；
挑战的持久化与消费由 `main.py` + `RunStore` 负责（一次性、带过期）。
"""
from __future__ import annotations

import os
from typing import Any, Iterable, Optional

from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url, options_to_json_dict
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    AuthenticatorTransport,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

CHALLENGE_TTL_SECONDS = 300
TIMEOUT_MS = 120_000

RP_ID = (os.getenv("DR_WEBAUTHN_RP_ID") or "localhost").strip()
RP_NAME = (os.getenv("DR_WEBAUTHN_RP_NAME") or "爱考据 iKaoju").strip()
ORIGINS = tuple(
    origin.strip()
    for origin in (
        os.getenv("DR_WEBAUTHN_ORIGIN")
        or "http://localhost:5173,http://localhost:8000"
    ).split(",")
    if origin.strip()
)


def enabled() -> bool:
    """RP ID / 允许来源均有效才启用；未配置时端点 503（fail-closed，不静默降级）。"""
    return bool(RP_ID and ORIGINS)


def rp_id() -> str:
    return RP_ID


def rp_name() -> str:
    return RP_NAME


def origins() -> list[str]:
    return list(ORIGINS)


def _descriptor(row: dict[str, Any]) -> Optional[PublicKeyCredentialDescriptor]:
    credential_id = row.get("credential_id")
    if not isinstance(credential_id, (bytes, bytearray)) or not credential_id:
        return None
    transports: list[AuthenticatorTransport] = []
    raw_transports = row.get("transports") or []
    if isinstance(raw_transports, str):
        raw_transports = [raw_transports]
    for item in raw_transports:
        try:
            transports.append(AuthenticatorTransport(str(item)))
        except ValueError:
            continue
    return PublicKeyCredentialDescriptor(id=bytes(credential_id), transports=transports or None)


def registration_options(user_id: str, email: str,
                         existing: Iterable[dict[str, Any]]) -> tuple[bytes, dict[str, Any]]:
    """生成注册选项；challenge 由调用方落库（一次性）。"""
    exclude = [descriptor for row in existing if (descriptor := _descriptor(row)) is not None]
    challenge = os.urandom(32)
    options = generate_registration_options(
        rp_id=RP_ID,
        rp_name=RP_NAME,
        user_id=user_id.encode("utf-8"),
        user_name=email,
        user_display_name=email,
        challenge=challenge,
        timeout=TIMEOUT_MS,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.PREFERRED,
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
        exclude_credentials=exclude or None,
    )
    return challenge, options_to_json_dict(options)


def authentication_options(credentials: Iterable[dict[str, Any]]) -> tuple[bytes, dict[str, Any]]:
    """生成登录选项；空 allowCredentials = 使用可发现凭据（usernameless）。"""
    allow = [descriptor for row in credentials if (descriptor := _descriptor(row)) is not None]
    challenge = os.urandom(32)
    options = generate_authentication_options(
        rp_id=RP_ID,
        challenge=challenge,
        timeout=TIMEOUT_MS,
        allow_credentials=allow or None,
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    return challenge, options_to_json_dict(options)


def verify_registration(credential: dict[str, Any], challenge: bytes):
    return verify_registration_response(
        credential=credential,
        expected_challenge=challenge,
        expected_rp_id=RP_ID,
        expected_origin=list(ORIGINS),
        require_user_verification=True,
    )


def verify_authentication(credential: dict[str, Any], challenge: bytes,
                          public_key: bytes, sign_count: int):
    return verify_authentication_response(
        credential=credential,
        expected_challenge=challenge,
        expected_rp_id=RP_ID,
        expected_origin=list(ORIGINS),
        credential_public_key=public_key,
        credential_current_sign_count=sign_count,
        require_user_verification=True,
    )


def credential_id_b64(value: bytes) -> str:
    return bytes_to_base64url(value)


def credential_id_from_payload(payload: dict[str, Any]) -> bytes:
    raw = payload.get("rawId") or payload.get("id")
    if not isinstance(raw, str) or not raw:
        raise ValueError("missing credential id")
    return base64url_to_bytes(raw)


def transports_from_payload(payload: dict[str, Any]) -> list[str]:
    raw = payload.get("transports")
    if not raw and isinstance(payload.get("response"), dict):
        raw = payload["response"].get("transports")
    if not isinstance(raw, list):
        return []
    allowed = {item.value for item in AuthenticatorTransport}
    return [item for item in raw if isinstance(item, str) and item in allowed]


def device_type(value: Any) -> str:
    return str(getattr(value, "value", value) or "single_device")
