"""需求 24：邮件通道单测（假 smtplib，零网络）。"""
from __future__ import annotations

import smtplib

import pytest

from config import config
from web.backend.mailer import SMTPMailer, get_mailer


class _FakeSMTPBase:
    instances: list = []

    def __init__(self, host, port, timeout=None, context=None):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.context = context
        self.started_tls = False
        self.logged_in = None
        self.sent: list = []
        type(self).instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self, *, context=None):
        self.started_tls = True
        self.starttls_context = context

    def login(self, user, password):
        self.logged_in = (user, password)

    def send_message(self, message):
        self.sent.append(message)


class _FakeSMTPSSL(_FakeSMTPBase):
    instances: list = []


class _FakeSMTP(_FakeSMTPBase):
    instances: list = []


@pytest.fixture
def fake_smtp(monkeypatch):
    _FakeSMTPSSL.instances = []
    _FakeSMTP.instances = []
    monkeypatch.setattr(smtplib, "SMTP_SSL", _FakeSMTPSSL)
    monkeypatch.setattr(smtplib, "SMTP", _FakeSMTP)
    return _FakeSMTPSSL, _FakeSMTP


def _mailer(**overrides) -> SMTPMailer:
    params = {"host": "smtp.example.com", "port": 465, "user": "u",
              "password": "p", "sender": "no-reply@example.com", "tls": "ssl"}
    params.update(overrides)
    return SMTPMailer(**params)


def test_ssl_mode_sends_message(fake_smtp):
    ssl_cls, plain_cls = fake_smtp
    _mailer(tls="ssl").send("user@example.com", "主题", "正文", "<p>正文</p>")

    assert len(ssl_cls.instances) == 1 and not plain_cls.instances
    client = ssl_cls.instances[0]
    assert (client.host, client.port) == ("smtp.example.com", 465)
    assert client.logged_in == ("u", "p")
    message = client.sent[0]
    assert message["To"] == "user@example.com"
    assert message["Subject"] == "主题"
    assert "no-reply@example.com" in message["From"]
    body = message.get_body(preferencelist=("plain",))
    assert "正文" in body.get_content()
    parts = list(message.iter_parts())
    assert any(part.get_content_type() == "text/html" for part in parts)


def test_starttls_mode_calls_starttls_before_login(fake_smtp):
    ssl_cls, plain_cls = fake_smtp
    _mailer(tls="starttls", port=587).send("user@example.com", "s", "t")

    assert not ssl_cls.instances and len(plain_cls.instances) == 1
    client = plain_cls.instances[0]
    assert client.started_tls is True
    assert client.logged_in == ("u", "p")


def test_none_mode_skips_starttls_for_mailhog(fake_smtp):
    ssl_cls, plain_cls = fake_smtp
    _mailer(tls="none", port=1025).send("user@example.com", "s", "t")

    client = plain_cls.instances[0]
    assert client.started_tls is False
    assert client.sent  # 无认证本地 sandbox 仍应投递


def test_no_login_when_user_empty(fake_smtp):
    ssl_cls, _ = fake_smtp
    _mailer(user="", password="").send("user@example.com", "s", "t")

    assert ssl_cls.instances[0].logged_in is None


def test_send_failure_propagates(fake_smtp):
    ssl_cls, _ = fake_smtp

    def _boom(self, message):
        raise smtplib.SMTPException("relay denied")

    ssl_cls.send_message = _boom
    with pytest.raises(smtplib.SMTPException):
        _mailer().send("user@example.com", "s", "t")


def test_get_mailer_unconfigured_returns_none(monkeypatch):
    monkeypatch.setattr(config.mail, "host", "")
    monkeypatch.setattr(config.mail, "sender", "")
    assert get_mailer() is None


def test_get_mailer_configured_returns_smtp_mailer(monkeypatch):
    monkeypatch.setattr(config.mail, "host", "smtp.example.com")
    monkeypatch.setattr(config.mail, "sender", "no-reply@example.com")
    assert isinstance(get_mailer(), SMTPMailer)
