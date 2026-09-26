"""P4-A 账号与会话原语测试（纯函数，零外部服务）。"""
from __future__ import annotations

from web.backend.auth import (
    hash_password,
    is_valid_email,
    new_invite_code,
    new_token,
    normalize_email,
    token_hash,
    verify_password,
)


def test_password_hash_is_argon2id_and_verifies():
    hashed = hash_password("correct horse battery")
    assert hashed.startswith("$argon2id$")
    assert verify_password(hashed, "correct horse battery") is True
    assert verify_password(hashed, "wrong password") is False


def test_password_hashes_are_salted():
    assert hash_password("same-password") != hash_password("same-password")


def test_verify_password_tolerates_garbage_hash():
    assert verify_password("not-a-hash", "whatever") is False


def test_token_hash_is_stable_sha256():
    token = new_token()
    digest = token_hash(token)
    assert digest == token_hash(token)
    assert len(digest) == 64
    assert token not in digest


def test_invite_codes_are_random():
    assert new_invite_code() != new_invite_code()


def test_email_normalize_and_validate():
    assert normalize_email("  User@Example.COM ") == "user@example.com"
    assert is_valid_email("user@example.com") is True
    assert is_valid_email("user@example") is False
    assert is_valid_email("no spaces@example.com") is False
