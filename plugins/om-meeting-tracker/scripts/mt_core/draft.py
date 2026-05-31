from __future__ import annotations

from typing import Protocol

from mt_core.config import Config
from mt_core.tracking import TrackedMetric


class Report(Protocol):
    owner_id: str
    metric_ids: list[str]
    text: str


def _oneline(text: str) -> str:
    return " ".join(text.split())[:280]


def render_draft(config: Config, tracked: list[TrackedMetric], week: str,
                 reports: list) -> str:
    tracked_by_id = {t.metric_id: t for t in tracked}
    owners_by_id = {o.owner_id: o for o in config.owners}

    reports_by_metric: dict[str, list] = {}
    unassigned: list = []
    for r in reports:
        if r.metric_ids:
            for mid in r.metric_ids:
                reports_by_metric.setdefault(mid, []).append(r)
        else:
            unassigned.append(r)

    out = [
        f"# 準會議版 draft — {week}", "",
        "> Routine 自動草擬，僅含 owner 回報內容；達成率留待會議前由主管彙整（不灌水）。", "",
    ]
    pending: list[str] = []
    for metric in config.metrics:
        owner = owners_by_id.get(metric.owner_id)
        owner_name = owner.name if owner else metric.owner_id
        tm = tracked_by_id.get(metric.metric_id)
        rag = tm.rag if (tm and tm.rag) else "—"
        out += [
            f"## {metric.title}（{owner_name}）",
            f"- RAG：{rag}　達成率：⏳ 待會議　deadline：{metric.deadline.isoformat()}",
        ]
        reps = reports_by_metric.get(metric.metric_id, [])
        if reps:
            for r in reps:
                out.append(f"- 回報：{_oneline(r.text)} (source: owner email)")
        else:
            out.append("- 回報：（尚無，列入待回填）")
            pending.append(f"{metric.title}（{owner_name}）")
        out.append("")

    out += ["## ⚠️ 待回填", ""]
    out += ([f"- {p}" for p in pending] if pending else ["- （全部已回報）"])
    if unassigned:
        out += ["", "## 未指定指標的回報"]
        out += [f"- {_oneline(r.text)}（owner: {r.owner_id}）" for r in unassigned]
    return "\n".join(out) + "\n"
