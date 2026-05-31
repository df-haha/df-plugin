# tests/test_send_adapters.py
from __future__ import annotations
import pytest
from mt_core.config import SendCfg
from mt_core.send_adapters import N8nWebhookAdapter, GmailSmtpAdapter, get_adapter, SendResult

def test_n8n_adapter_sends_key_and_secret_header():
    calls = {}
    def fake_post(url, payload, headers):
        calls.update(url=url, payload=payload, headers=headers)
        return {"provider_message_id": "n8n-123"}
    a = N8nWebhookAdapter(webhook_url="https://n8n.example/webhook/gmail-send",
                          webhook_secret="s3cr3t", post_fn=fake_post)
    r = a.send(to="x@example.com", subject="hi", body_text="t", body_html="<p>t</p>",
               idempotency_key="MT:acme:digest:alice:2026-W22:2026-05-29")
    assert isinstance(r, SendResult) and r.provider_message_id == "n8n-123"
    assert calls["payload"]["to"] == "x@example.com"
    assert calls["payload"]["idempotency_key"] == "MT:acme:digest:alice:2026-W22:2026-05-29"
    assert calls["headers"]["X-MT-Webhook-Secret"] == "s3cr3t"
    assert calls["url"].endswith("/webhook/gmail-send")

def test_n8n_adapter_fallback_id_key():
    a = N8nWebhookAdapter(webhook_url="https://n8n.example/w", webhook_secret="x",
                          post_fn=lambda u, p, h: {"id": "alt-9"})
    assert a.send(to="x@x.com", subject="s", body_text="t", body_html="h",
                  idempotency_key="k").provider_message_id == "alt-9"

def test_gmail_message_id_deterministic_from_key():
    import re
    captured = {}
    def fake_smtp_send(user, to, raw):
        captured["raw"] = raw
    a = GmailSmtpAdapter(user="u@gmail.com", app_password="pw", send_fn=fake_smtp_send)
    a.send(to="x@x.com", subject="s", body_text="t", body_html="h", idempotency_key="K1")
    mid1 = re.search(r"Message-ID:\s*(<[^>]+>)", captured["raw"]).group(1)
    a.send(to="x@x.com", subject="s", body_text="t", body_html="h", idempotency_key="K1")
    mid2 = re.search(r"Message-ID:\s*(<[^>]+>)", captured["raw"]).group(1)
    assert mid1 == mid2  # 同 idempotency_key → 同 Message-ID（deterministic、可去重）

def test_get_adapter_dispatch(monkeypatch):
    monkeypatch.setenv("MT_N8N_WEBHOOK_URL", "https://n8n.example/w")
    monkeypatch.setenv("MT_N8N_WEBHOOK_SECRET", "s")
    assert isinstance(get_adapter(SendCfg("n8n_webhook")), N8nWebhookAdapter)
    monkeypatch.setenv("MT_GMAIL_USER", "u@gmail.com")
    monkeypatch.setenv("MT_GMAIL_APP_PASSWORD", "app-pw")
    assert isinstance(get_adapter(SendCfg("gmail_smtp")), GmailSmtpAdapter)

def test_get_adapter_unknown_raises():
    with pytest.raises(ValueError, match="未知 send adapter"):
        get_adapter(SendCfg("carrier-pigeon"))
