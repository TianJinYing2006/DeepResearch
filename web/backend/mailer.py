"""需求 24：邮件通道（SMTP-first，stdlib 零依赖）。

- :class:`SMTPMailer`：``ssl``（465）/ ``starttls``（587）/ ``none``（本地 MailHog）三模式；
- :func:`get_mailer`：未配置（host / sender 为空）返回 ``None`` —— 调用方结构化降级
  （``mail_unavailable`` 503），不做静默吞错；
- 凭据只来自环境变量；日志/审计不得打印凭据、token、完整邮箱。
"""
from __future__ import annotations

import smtplib
import ssl as ssl_module
from email.message import EmailMessage
from email.utils import formataddr
from typing import Optional, Protocol

from config import config

#: 单次 SMTP 交互超时（秒）；避免 forgot 端点被慢邮件服务商拖死
SEND_TIMEOUT_SECONDS = 15.0


class Mailer(Protocol):
    def send(self, to: str, subject: str, text: str, html: Optional[str] = None) -> None: ...


class SMTPMailer:
    """stdlib SMTP 实现；发送异常上抛，由调用方审计。"""

    def __init__(self, *, host: str, port: int, user: str, password: str,
                 sender: str, tls: str, timeout: float = SEND_TIMEOUT_SECONDS):
        self._host = host
        self._port = port
        self._user = user
        self._password = password
        self._sender = sender
        self._tls = tls
        self._timeout = timeout

    def send(self, to: str, subject: str, text: str, html: Optional[str] = None) -> None:
        message = EmailMessage()
        message["From"] = formataddr(("DeepResearch", self._sender))
        message["To"] = to
        message["Subject"] = subject
        message.set_content(text)
        if html:
            message.add_alternative(html, subtype="html")
        if self._tls == "ssl":
            with smtplib.SMTP_SSL(self._host, self._port, timeout=self._timeout,
                                  context=ssl_module.create_default_context()) as client:
                self._login_and_send(client, message)
            return
        with smtplib.SMTP(self._host, self._port, timeout=self._timeout) as client:
            if self._tls == "starttls":
                client.starttls(context=ssl_module.create_default_context())
            self._login_and_send(client, message)

    def _login_and_send(self, client, message: EmailMessage) -> None:
        if self._user:
            client.login(self._user, self._password)
        client.send_message(message)


def get_mailer() -> Optional[Mailer]:
    """按配置构造 Mailer；未配置返回 ``None``（调用方 503 降级）。"""
    mail = config.mail
    if not mail.configured:
        return None
    return SMTPMailer(host=mail.host, port=mail.port, user=mail.user,
                      password=mail.password, sender=mail.sender, tls=mail.tls)


def password_reset_email(link: str, *, minutes: int = 30) -> tuple[str, str, str]:
    """重置邮件模板（纯文本 + 简 HTML）：不展示用户名/邮箱，不索要密码。"""
    subject = "重置你的 DeepResearch 密码"
    text = (
        "我们收到了重置密码的请求。\n\n"
        f"请在 {minutes} 分钟内打开以下链接设置新密码：\n{link}\n\n"
        "链接仅可使用一次；重置后所有设备都会退出登录。\n"
        "如果这不是你本人的操作，忽略本邮件即可，你的密码不会被修改。"
    )
    html = (
        "<p>我们收到了重置密码的请求。</p>"
        f"<p>请在 {minutes} 分钟内点击链接设置新密码：<br>"
        f'<a href="{link}">重置密码</a></p>'
        "<p>链接仅可使用一次；重置后所有设备都会退出登录。<br>"
        "如果这不是你本人的操作，忽略本邮件即可，你的密码不会被修改。</p>"
    )
    return subject, text, html
