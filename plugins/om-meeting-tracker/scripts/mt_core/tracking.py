# scripts/mt_core/tracking.py
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

_ANCHOR = re.compile(r"<!--\s*mt:metric\s+(?P<attrs>.*?)-->")
_KV = re.compile(r"(\w+)=(\S*)")


@dataclass
class TrackedMetric:
    metric_id: str
    owner_id: str
    rag: str | None
    achieved: str | None
    deadline: date | None
    raw_attrs: dict


def _parse_attrs(s: str) -> dict:
    return {k: v for k, v in _KV.findall(s)}


def parse_metrics(md_text: str) -> list[TrackedMetric]:
    out: list[TrackedMetric] = []
    for m in _ANCHOR.finditer(md_text):
        a = _parse_attrs(m.group("attrs"))
        dl: date | None = None
        if a.get("deadline"):
            try:
                dl = date.fromisoformat(a["deadline"])
            except ValueError:
                dl = None
        out.append(TrackedMetric(
            metric_id=a.get("id", ""),
            owner_id=a.get("owner", ""),
            rag=(a.get("rag") or None),
            achieved=(a.get("achieved") or None),
            deadline=dl,
            raw_attrs=a,
        ))
    return out
