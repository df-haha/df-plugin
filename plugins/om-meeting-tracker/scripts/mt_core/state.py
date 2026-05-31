# scripts/mt_core/state.py
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

SCHEMA_VERSION = 1


def reminder_key(tenant_id: str, metric_id: str, week: str, date_str: str) -> str:
    return f"MT:{tenant_id}:{metric_id}:{week}:{date_str}"


def default_state(tenant_id: str) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "tenant_id": tenant_id,
        "last_run_at": None,
        "last_human_reviewed": {},
        "sent_reminders": {},
        "processed_replies": {},
        "metric_last_nudge": {},
        "retry": {},
    }


def load_state(path: Path, tenant_id: str) -> dict:
    path = Path(path)
    if not path.exists():
        return default_state(tenant_id)
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"state schema_version 不符：{data.get('schema_version')!r}")
    return data


def save_state(path: Path, state: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def already_sent(state: dict, key: str) -> bool:
    return key in state["sent_reminders"]


def record_sent(state: dict, key: str, **fields) -> None:
    state["sent_reminders"][key] = dict(fields)


def already_processed_reply(state: dict, msg_id: str) -> bool:
    return msg_id in state["processed_replies"]


def record_reply(state: dict, msg_id: str, **fields) -> None:
    state["processed_replies"][msg_id] = dict(fields)


def set_human_reviewed(state: dict, blob_sha: str, commit_sha: str, at_iso: str) -> None:
    state["last_human_reviewed"] = {
        "tracking_file_blob_sha": blob_sha,
        "tracking_file_commit_sha": commit_sha,
        "at": at_iso,
    }
