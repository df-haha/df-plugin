# scripts/mt_core/send_adapters.py
from __future__ import annotations

import hashlib
import json
import os
import smtplib
import ssl
from dataclasses import dataclass
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Callable, Protocol
from urllib import request as _urlrequest


@dataclass
class SendResult:
    provider_message_id: str


class SendAdapter(Protocol):
    def send(self, *, to: str, subject: str, body_text: str, body_html: str,
             idempotency_key: str) -> SendResult: ...


class N8nWebhookAdapter:
    """Routine/本機 → n8n webhook → n8n Gmail node 寄信。URL/secret 從 env 讀，不進 repo。"""

    def __init__(self, webhook_url: str | None = None, webhook_secret: str | None = None,
                 post_fn: Callable[[str, dict, dict], dict] | None = None) -> None:
        self.webhook_url = webhook_url or os.environ["MT_N8N_WEBHOOK_URL"]
        self.webhook_secret = (webhook_secret if webhook_secret is not None
                               else os.environ.get("MT_N8N_WEBHOOK_SECRET", ""))
        self._post = post_fn or self._default_post

    @staticmethod
    def _default_post(url: str, payload: dict, headers: dict) -> dict:
        data = json.dumps(payload).encode("utf-8")
        req = _urlrequest.Request(url, data=data, headers=headers, method="POST")
        with _urlrequest.urlopen(req, timeout=30) as resp:  # noqa: S310 (固定 https endpoint)
            body = resp.read().decode("utf-8")
        return json.loads(body) if body.strip() else {}

    def send(self, *, to: str, subject: str, body_text: str, body_html: str,
             idempotency_key: str) -> SendResult:
        payload = {"to": to, "subject": subject, "text": body_text, "html": body_html,
                   "idempotency_key": idempotency_key}
        headers = {"Content-Type": "application/json",
                   "X-MT-Webhook-Secret": self.webhook_secret}
        resp = self._post(self.webhook_url, payload, headers)
        mid = resp.get("provider_message_id") or resp.get("id") or ""
        return SendResult(provider_message_id=str(mid))


class GmailSmtpAdapter:
    """per-tenant Gmail SMTP（app password）。憑證從 env 讀，不進 repo。"""

    def __init__(self, user: str | None = None, app_password: str | None = None,
                 send_fn: Callable[[str, str, str], None] | None = None) -> None:
        self.user = user or os.environ["MT_GMAIL_USER"]
        self.app_password = app_password or os.environ["MT_GMAIL_APP_PASSWORD"]
        self._send_fn = send_fn or self._default_send

    def _default_send(self, user: str, to: str, raw: str) -> None:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context()) as s:
            s.login(self.user, self.app_password)
            s.sendmail(user, [to], raw)

    def send(self, *, to: str, subject: str, body_text: str, body_html: str,
             idempotency_key: str) -> SendResult:
        # deterministic Message-ID：同 idempotency_key → 同 id（重送可被收件端辨識去重）
        digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:32]
        domain = self.user.split("@")[-1] or "localhost"
        mid = f"<mt-{digest}@{domain}>"
        msg = MIMEMultipart("alternative")
        msg["Message-ID"] = mid
        msg["From"] = self.user
        msg["To"] = to
        msg["Subject"] = subject
        msg.attach(MIMEText(body_text, "plain", "utf-8"))
        msg.attach(MIMEText(body_html, "html", "utf-8"))
        self._send_fn(self.user, to, msg.as_string())
        return SendResult(provider_message_id=mid)


def get_adapter(send_cfg) -> SendAdapter:
    if send_cfg.adapter == "n8n_webhook":
        return N8nWebhookAdapter()
    if send_cfg.adapter == "gmail_smtp":
        return GmailSmtpAdapter()
    raise ValueError(f"未知 send adapter：{send_cfg.adapter!r}")
