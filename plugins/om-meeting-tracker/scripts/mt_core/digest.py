# scripts/mt_core/digest.py
from __future__ import annotations

import secrets
from dataclasses import dataclass

from mt_core.reminders import OwnerReminder


@dataclass
class Digest:
    to: str
    subject: str
    body_text: str
    body_html: str
    token: str


def correlation_token(tenant_id: str, owner_id: str, week: str, nonce: str | None = None) -> str:
    nonce = nonce or secrets.token_hex(3)
    return f"MTD1.{tenant_id}.{owner_id}.{week}.{nonce}"


def compose_digest(reminder: OwnerReminder, tenant_id: str, week: str,
                   token: str | None = None) -> Digest:
    owner = reminder.owner
    token = token or correlation_token(tenant_id, owner.owner_id, week)
    subject = f"[會議追蹤] {owner.name} 本週進度回報 ({week}) [#{token}]"

    text = [f"{owner.name} 你好，以下是本週（{week}）需要你回報的指標：", ""]
    html = [f"<p>{owner.name} 你好，以下是本週（{week}）需要你回報的指標：</p>"]
    for metric, _tracked in reminder.metrics:
        dl = metric.deadline.isoformat()
        text += [
            f"■ {metric.title} [#metric:{metric.metric_id}]（deadline {dl}）",
            "  - 目前進度：",
            "  - 卡關：",
            "  - 預計達成率（會議前估）：",
            "",
        ]
        html.append(
            f"<h4>{metric.title} <span style='color:#888'>[#metric:{metric.metric_id}]</span>"
            f"（deadline {dl}）</h4>"
            "<ul><li>目前進度：</li><li>卡關：</li><li>預計達成率（會議前估）：</li></ul>"
        )
    footer = "（直接回覆本信即可，請保留主旨。達成率由主管於會議前彙整，無需自行填精確數字。）"
    text += ["", footer]
    html.append(f"<p style='color:#888'>{footer}</p>")
    return Digest(to=owner.email, subject=subject,
                  body_text="\n".join(text), body_html="\n".join(html), token=token)
