# scripts/mt_core/reminders.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from mt_core.config import Config, Owner, Metric
from mt_core.tracking import TrackedMetric
from mt_core.timeutil import is_business_day

NEAR_DEADLINE_DAYS = 2
STALE_NUDGE_DAYS = 3


@dataclass
class OwnerReminder:
    owner: Owner
    metrics: list[tuple[Metric, TrackedMetric | None]]


def _parse_snooze(cadence: str) -> date | None:
    if cadence.startswith("snooze:"):
        try:
            return date.fromisoformat(cadence.split(":", 1)[1])
        except ValueError:
            return None
    return None


def should_remind(metric: Metric, tracked: TrackedMetric | None, today: date,
                  config: Config, state: dict) -> bool:
    rag = tracked.rag if tracked else None
    deadline = (tracked.deadline if tracked and tracked.deadline else metric.deadline)

    # 強制催（覆蓋 cadence）
    if rag == "red":
        return True
    if deadline and (deadline - today) <= timedelta(days=NEAR_DEADLINE_DAYS):
        return True
    last = (state.get("metric_last_nudge") or {}).get(metric.metric_id)
    if last:
        try:
            if (today - date.fromisoformat(last)) >= timedelta(days=STALE_NUDGE_DAYS):
                return True
        except ValueError:
            pass

    # cadence
    c = metric.cadence
    if c == "daily":
        return True
    if c == "business_days":
        return is_business_day(today, config.business_days)
    if c == "overdue_only":
        return bool(deadline and today > deadline) or rag == "red"
    snooze = _parse_snooze(c)
    if snooze is not None:
        return today >= snooze
    return False


def compute_reminders(config: Config, tracked: list[TrackedMetric], today: date,
                      state: dict) -> list[OwnerReminder]:
    tracked_by_id = {t.metric_id: t for t in tracked}
    by_owner: dict[str, list[tuple[Metric, TrackedMetric | None]]] = {}
    for metric in config.metrics:
        tm = tracked_by_id.get(metric.metric_id)
        if should_remind(metric, tm, today, config, state):
            by_owner.setdefault(metric.owner_id, []).append((metric, tm))
    owners_by_id = {o.owner_id: o for o in config.owners}
    result = [OwnerReminder(owner=owners_by_id[oid], metrics=ms) for oid, ms in by_owner.items()]
    result.sort(key=lambda r: r.owner.owner_id)
    return result
