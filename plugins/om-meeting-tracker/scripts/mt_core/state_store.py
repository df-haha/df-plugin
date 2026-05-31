# scripts/mt_core/state_store.py
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Callable

from mt_core.state import load_state as _file_load, save_state as _file_save, default_state


def load_state_for(config, repo_root) -> dict:
    if config.state_backend == "postgres":
        return PostgresStore(config.tenant_id).load()
    return _file_load(Path(repo_root) / config.paths.state_file, config.tenant_id)


def save_state_for(config, repo_root, state: dict) -> None:
    if config.state_backend == "postgres":
        PostgresStore(config.tenant_id).save(state)
        return
    _file_save(Path(repo_root) / config.paths.state_file, state)


class PostgresStore:
    """state JSONB UPSERT 到 meeting_tracker.tenant_state；連線從 env 讀（寫權限、不進 repo）。

    DDL（onboarding/M0.5 先建）：
      CREATE SCHEMA IF NOT EXISTS meeting_tracker;
      CREATE TABLE IF NOT EXISTS meeting_tracker.tenant_state (
          tenant_id text PRIMARY KEY, state jsonb NOT NULL,
          updated_at timestamptz NOT NULL DEFAULT now());
    ⚠️ 用 meeting_tracker schema，**不可用 cockpit**（harness 紅線標 cockpit 唯讀）。
    """

    def __init__(self, tenant_id: str, connect: Callable[[], object] | None = None) -> None:
        self.tenant_id = tenant_id
        self._connect = connect or self._default_connect

    @staticmethod
    def _default_connect():
        import psycopg2  # 僅 postgres backend 需要；plugin 預設 git_branch 不依賴
        return psycopg2.connect(os.environ["MT_STATE_DATABASE_URL"])

    def load(self) -> dict:
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT state FROM meeting_tracker.tenant_state WHERE tenant_id=%s",
                    (self.tenant_id,))
                row = cur.fetchone()
            return row[0] if row else default_state(self.tenant_id)
        finally:
            conn.close()

    def save(self, state: dict) -> None:
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO meeting_tracker.tenant_state (tenant_id, state) "
                    "VALUES (%s, %s::jsonb) "
                    "ON CONFLICT (tenant_id) DO UPDATE "
                    "SET state = EXCLUDED.state, updated_at = now()",
                    (self.tenant_id, json.dumps(state, ensure_ascii=False)))
            conn.commit()
        finally:
            conn.close()
