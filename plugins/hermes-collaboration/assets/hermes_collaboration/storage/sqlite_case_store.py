"""Transactional SQLite WAL ledger hidden behind the CaseCoordinator."""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence


class ReplayConflictError(RuntimeError):
    pass


class SQLiteCaseStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._init()
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA busy_timeout=10000")
        return con

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        con = self._connect()
        try:
            con.execute("BEGIN IMMEDIATE")
            yield con
            con.commit()
        except BaseException:
            con.rollback()
            raise
        finally:
            con.close()

    def _init(self) -> None:
        with self._connect() as con:
            con.executescript("""
            CREATE TABLE IF NOT EXISTS cases(case_id TEXT PRIMARY KEY,state TEXT NOT NULL,view_json TEXT NOT NULL,updated_at TEXT NOT NULL,terminal INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS events(event_id TEXT PRIMARY KEY,case_id TEXT NOT NULL,origin_id TEXT NOT NULL,origin_seq INTEGER NOT NULL,content_hash TEXT NOT NULL,event_json TEXT NOT NULL,UNIQUE(origin_id,origin_seq));
            CREATE TABLE IF NOT EXISTS commands(command_id TEXT PRIMARY KEY,content_hash TEXT NOT NULL,case_id TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS inbox(delivery_id TEXT PRIMARY KEY,content_hash TEXT NOT NULL,outcome TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS fragments(sender_id INTEGER NOT NULL,manifest TEXT NOT NULL,fragment_index INTEGER NOT NULL,fragment_count INTEGER NOT NULL,fragment_text TEXT NOT NULL,received_at TEXT NOT NULL,PRIMARY KEY(sender_id,manifest,fragment_index));
            CREATE TABLE IF NOT EXISTS outbox(id INTEGER PRIMARY KEY AUTOINCREMENT,case_id TEXT NOT NULL,kind TEXT NOT NULL,payload_json TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'pending',attempts INTEGER NOT NULL DEFAULT 0,last_error TEXT);
            CREATE TABLE IF NOT EXISTS work(work_id TEXT PRIMARY KEY,case_id TEXT NOT NULL,capability TEXT NOT NULL,executor TEXT NOT NULL,payload_json TEXT NOT NULL,status TEXT NOT NULL,lease_owner TEXT,authority_ref TEXT NOT NULL,candidate_revision TEXT);
            CREATE TABLE IF NOT EXISTS policies(policy_id TEXT PRIMARY KEY,revision INTEGER NOT NULL,status TEXT NOT NULL,payload_json TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS grants(grant_id TEXT PRIMARY KEY,case_id TEXT NOT NULL,status TEXT NOT NULL,payload_json TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS questions(question_id TEXT PRIMARY KEY,case_id TEXT NOT NULL,job_id TEXT NOT NULL,grant_id TEXT NOT NULL,content_hash TEXT NOT NULL,status TEXT NOT NULL,payload_json TEXT NOT NULL,answer_id TEXT);
            CREATE TABLE IF NOT EXISTS candidates(candidate_revision TEXT PRIMARY KEY,case_id TEXT NOT NULL,base_revision TEXT NOT NULL,producing_job_id TEXT NOT NULL,status TEXT NOT NULL,payload_json TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS reviews(review_id TEXT PRIMARY KEY,case_id TEXT NOT NULL,candidate_revision TEXT NOT NULL,verdict TEXT NOT NULL,status TEXT NOT NULL,payload_json TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS findings(finding_id TEXT PRIMARY KEY,review_id TEXT NOT NULL,case_id TEXT NOT NULL,accepted INTEGER NOT NULL DEFAULT 0,payload_json TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS merge_decisions(decision_id TEXT PRIMARY KEY,case_id TEXT NOT NULL,candidate_revision TEXT NOT NULL,review_id TEXT NOT NULL,status TEXT NOT NULL,payload_json TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS discussions(mandate_id TEXT PRIMARY KEY,case_id TEXT NOT NULL,status TEXT NOT NULL,turn_count INTEGER NOT NULL,cost_used REAL NOT NULL,payload_json TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY AUTOINCREMENT,event_type TEXT NOT NULL,actor_category TEXT NOT NULL,authority_ref TEXT NOT NULL,content_hash TEXT NOT NULL,occurred_at TEXT NOT NULL,outcome TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS external_actions(action_key TEXT PRIMARY KEY,case_id TEXT NOT NULL,kind TEXT NOT NULL,status TEXT NOT NULL,payload_json TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS external_artifacts(artifact_key TEXT PRIMARY KEY,case_id TEXT NOT NULL,payload_json TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS predecessor_attachments(source_trace_id TEXT PRIMARY KEY,case_id TEXT NOT NULL);
            """)

    def external_action(self, action_key: str) -> dict[str, Any] | None:
        with self._connect() as con:
            row = con.execute("SELECT payload_json,status FROM external_actions WHERE action_key=?", (action_key,)).fetchone()
        if not row:
            return None
        value = json.loads(row["payload_json"])
        value["status"] = row["status"]
        return value

    def record_external_action(self, action_key: str, case_id: str, kind: str, status: str, payload: Mapping[str, Any]) -> bool:
        with self._write() as con:
            prior = con.execute("SELECT case_id,kind,payload_json FROM external_actions WHERE action_key=?", (action_key,)).fetchone()
            body = json.dumps(dict(payload), sort_keys=True)
            if prior:
                if prior["case_id"] != case_id or prior["kind"] != kind or prior["payload_json"] != body:
                    raise ReplayConflictError("external action identity replayed with different content")
                con.execute("UPDATE external_actions SET status=? WHERE action_key=?", (status, action_key))
                return False
            con.execute("INSERT INTO external_actions VALUES(?,?,?,?,?)", (action_key, case_id, kind, status, body))
            return True

    def confirm_external_artifact(
        self,
        action_key: str,
        case_id: str,
        payload: Mapping[str, Any],
        *,
        artifact_key: str | None = None,
    ) -> None:
        with self._write() as con:
            action = con.execute("SELECT case_id FROM external_actions WHERE action_key=?", (action_key,)).fetchone()
            if not action or action["case_id"] != case_id:
                raise ReplayConflictError("external action intent is missing")
            body = json.dumps(dict(payload), sort_keys=True)
            resolved_artifact_key = artifact_key or action_key
            prior = con.execute("SELECT case_id,payload_json FROM external_artifacts WHERE artifact_key=?", (resolved_artifact_key,)).fetchone()
            if prior and prior["case_id"] != case_id:
                raise ReplayConflictError("external artifact identity replayed for another Case")
            con.execute(
                "INSERT INTO external_artifacts VALUES(?,?,?) ON CONFLICT(artifact_key) DO UPDATE SET payload_json=excluded.payload_json",
                (resolved_artifact_key, case_id, body),
            )
            action_result = {key: value for key, value in payload.items() if key not in {"artifact_type", "candidate_revision", "source_trace_id"}}
            con.execute(
                "UPDATE external_actions SET status='confirmed',payload_json=? WHERE action_key=?",
                (json.dumps(action_result, sort_keys=True), action_key),
            )

    def record_external_artifact(self, artifact_key: str, case_id: str, payload: Mapping[str, Any]) -> None:
        with self._write() as con:
            body = json.dumps(dict(payload), sort_keys=True)
            prior = con.execute("SELECT case_id,payload_json FROM external_artifacts WHERE artifact_key=?", (artifact_key,)).fetchone()
            if prior:
                if prior["case_id"] != case_id or prior["payload_json"] != body:
                    raise ReplayConflictError("external artifact identity replayed with different content")
                return
            con.execute("INSERT INTO external_artifacts VALUES(?,?,?)", (artifact_key, case_id, body))

    def external_artifacts(self, case_id: str) -> list[dict[str, Any]]:
        with self._connect() as con:
            rows = con.execute("SELECT payload_json FROM external_artifacts WHERE case_id=? ORDER BY rowid", (case_id,)).fetchall()
        return [json.loads(row["payload_json"]) for row in rows]

    def external_artifact(self, artifact_key: str) -> dict[str, Any] | None:
        with self._connect() as con:
            row = con.execute("SELECT payload_json FROM external_artifacts WHERE artifact_key=?", (artifact_key,)).fetchone()
        return json.loads(row["payload_json"]) if row else None

    def attach_predecessor(self, source_trace_id: str, case_id: str) -> None:
        with self._write() as con:
            prior = con.execute("SELECT case_id FROM predecessor_attachments WHERE source_trace_id=?", (source_trace_id,)).fetchone()
            if prior and prior["case_id"] != case_id:
                raise ReplayConflictError("source_trace_id is already attached to another Case")
            con.execute("INSERT OR IGNORE INTO predecessor_attachments VALUES(?,?)", (source_trace_id, case_id))

    def predecessor_case(self, source_trace_id: str) -> str | None:
        with self._connect() as con:
            row = con.execute("SELECT case_id FROM predecessor_attachments WHERE source_trace_id=?", (source_trace_id,)).fetchone()
        return str(row["case_id"]) if row else None

    def command_status(self, command_id: str, digest: str) -> str | None:
        with self._connect() as con:
            row = con.execute("SELECT content_hash,case_id FROM commands WHERE command_id=?", (command_id,)).fetchone()
        if not row: return None
        if row["content_hash"] != digest: raise ReplayConflictError("command ID replayed with different content")
        return str(row["case_id"])

    def delivery_seen(self, delivery_id: str, digest: str) -> bool:
        with self._connect() as con:
            row = con.execute("SELECT content_hash FROM inbox WHERE delivery_id=?", (delivery_id,)).fetchone()
        if not row:
            return False
        if row["content_hash"] != digest:
            raise ReplayConflictError("delivery ID replayed with different content")
        return True

    def next_sequence(self, origin_id: str) -> int:
        with self._connect() as con:
            row = con.execute("SELECT COALESCE(MAX(origin_seq),0)+1 n FROM events WHERE origin_id=?", (origin_id,)).fetchone()
        return int(row["n"])

    def append(self, *, case_id: str, state: str, event: Mapping[str, Any], precursor_events: Sequence[Mapping[str, Any]] = (), outbox: Sequence[Mapping[str, Any]] = (), view: Mapping[str, Any] | None = None, terminal: bool = False, command: tuple[str, str] | None = None, delivery: tuple[str, str] | None = None, work: Sequence[Mapping[str, Any]] = (), policy: Mapping[str, Any] | None = None, grant: Mapping[str, Any] | None = None, question: Mapping[str, Any] | None = None, answer: tuple[str, str] | None = None, candidate: Mapping[str, Any] | None = None, review: Mapping[str, Any] | None = None, accepted_finding_ids: Sequence[str] = (), merge_decision: Mapping[str, Any] | None = None, discussion: Mapping[str, Any] | None = None, predecessor_attachment: str | None = None, cancel_authority_ref: str | None = None, work_update: tuple[str, str, str] | None = None, case_work_status: str | None = None, audit_actor_category: str | None = None, audit_authority_ref: str | None = None) -> str:
        body = json.dumps(event, sort_keys=True, separators=(",", ":"))
        with self._write() as con:
            if delivery:
                prior_delivery = con.execute(
                    "SELECT content_hash FROM inbox WHERE delivery_id=?", (delivery[0],)
                ).fetchone()
                if prior_delivery:
                    if prior_delivery["content_hash"] != delivery[1]:
                        raise ReplayConflictError("delivery ID replayed with different content")
                    return "duplicate"
            row = con.execute("SELECT content_hash FROM events WHERE event_id=?", (event["event_id"],)).fetchone()
            if row:
                if row["content_hash"] != event["content_hash"]:
                    raise ReplayConflictError("event ID replayed with different content")
                if delivery:
                    con.execute("INSERT INTO inbox VALUES(?,?,?)", (delivery[0], delivery[1], "duplicate"))
                return "duplicate"
            if command:
                prior = con.execute("SELECT content_hash,case_id FROM commands WHERE command_id=?", (command[0],)).fetchone()
                if prior:
                    if prior["content_hash"] != command[1]: raise ReplayConflictError("command ID replayed with different content")
                    return "duplicate"
            current = con.execute("SELECT terminal,view_json FROM cases WHERE case_id=?", (case_id,)).fetchone()
            if current and current["terminal"] and event["event_type"] != "case_closed":
                raise ReplayConflictError("terminal Case rejects new work")
            snapshot = json.loads(current["view_json"]) if current else {"case_id": case_id, "state": state, "entries": []}
            snapshot.update(dict(view or {}))
            for precursor in precursor_events:
                precursor_body = json.dumps(precursor, sort_keys=True, separators=(",", ":"))
                con.execute("INSERT INTO events VALUES(?,?,?,?,?,?)", (precursor["event_id"], case_id, precursor["origin"]["representative_id"], precursor["origin"]["sequence"], precursor["content_hash"], precursor_body))
            con.execute("INSERT INTO events VALUES(?,?,?,?,?,?)", (event["event_id"], case_id, event["origin"]["representative_id"], event["origin"]["sequence"], event["content_hash"], body))
            if delivery:
                con.execute("INSERT INTO inbox VALUES(?,?,?)", (delivery[0], delivery[1], "accepted"))
            if command: con.execute("INSERT INTO commands VALUES(?,?,?)", (command[0], command[1], case_id))
            con.execute("INSERT INTO cases VALUES(?,?,?,?,?) ON CONFLICT(case_id) DO UPDATE SET state=excluded.state,view_json=excluded.view_json,updated_at=excluded.updated_at,terminal=excluded.terminal", (case_id, state, json.dumps(snapshot), datetime.now(timezone.utc).isoformat(), int(terminal)))
            for item in outbox:
                con.execute("INSERT INTO outbox(case_id,kind,payload_json) VALUES(?,?,?)", (case_id, item["kind"], json.dumps(item["payload"])))
            for item in work:
                con.execute(
                    "INSERT OR IGNORE INTO work VALUES(?,?,?,?,?,'queued',NULL,?,?)",
                    (item["work_id"], item["case_id"], item["capability"], item["executor"], json.dumps(item["payload"]), item["authority_ref"], item.get("candidate_revision")),
                )
            if policy:
                con.execute(
                    "INSERT INTO policies VALUES(?,?,?,?) ON CONFLICT(policy_id) DO UPDATE SET revision=excluded.revision,status=excluded.status,payload_json=excluded.payload_json",
                    (policy["policy_id"], policy["revision"], policy["status"], json.dumps(policy)),
                )
            if grant:
                con.execute(
                    "INSERT INTO grants VALUES(?,?,?,?)",
                    (grant["grant_id"], case_id, grant["status"], json.dumps(grant)),
                )
            if question:
                con.execute(
                    "INSERT INTO questions VALUES(?,?,?,?,?,'open',?,NULL)",
                    (question["question_id"], case_id, question["job_id"], question["grant_id"], event["content_hash"], json.dumps(question)),
                )
            if answer:
                changed = con.execute(
                    "UPDATE questions SET status='answered',answer_id=? WHERE question_id=? AND status='open'",
                    (answer[1], answer[0]),
                ).rowcount
                if changed != 1:
                    raise ReplayConflictError("question is not current and open")
            if candidate:
                con.execute("UPDATE candidates SET status='superseded' WHERE case_id=? AND status='current'", (case_id,))
                con.execute("UPDATE reviews SET status='stale' WHERE case_id=? AND status='current'", (case_id,))
                con.execute(
                    "INSERT INTO candidates VALUES(?,?,?,?,?,?)",
                    (candidate["candidate_revision"], case_id, candidate["base_revision"], candidate["job_id"], "current", json.dumps(candidate)),
                )
            if review:
                con.execute("UPDATE reviews SET status='superseded' WHERE case_id=? AND status='current'", (case_id,))
                con.execute(
                    "INSERT INTO reviews VALUES(?,?,?,?,?,?)",
                    (review["review_id"], case_id, review["candidate_revision"], review["verdict"], "current", json.dumps(review)),
                )
                for finding in review.get("findings", []):
                    con.execute("INSERT INTO findings VALUES(?,?,?,0,?)", (finding["finding_id"], review["review_id"], case_id, json.dumps(finding)))
            for finding_id in accepted_finding_ids:
                changed = con.execute("UPDATE findings SET accepted=1 WHERE finding_id=? AND case_id=?", (finding_id, case_id)).rowcount
                if changed != 1:
                    raise ReplayConflictError("finding is not current for this Case")
            if merge_decision:
                con.execute(
                    "INSERT INTO merge_decisions VALUES(?,?,?,?,?,?)",
                    (merge_decision["decision_id"], case_id, merge_decision["candidate_revision"], merge_decision["review_id"], "current", json.dumps(merge_decision)),
                )
            if discussion:
                con.execute(
                    "INSERT INTO discussions VALUES(?,?,?,?,?,?) ON CONFLICT(mandate_id) DO UPDATE SET status=excluded.status,turn_count=excluded.turn_count,cost_used=excluded.cost_used,payload_json=excluded.payload_json",
                    (discussion["mandate_id"], case_id, discussion["status"], discussion["turn_count"], discussion["cost_used"], json.dumps(discussion)),
                )
            if predecessor_attachment:
                prior_attachment = con.execute(
                    "SELECT case_id FROM predecessor_attachments WHERE source_trace_id=?", (predecessor_attachment,),
                ).fetchone()
                if prior_attachment and prior_attachment["case_id"] != case_id:
                    raise ReplayConflictError("source_trace_id is already attached to another Case")
                con.execute("INSERT OR IGNORE INTO predecessor_attachments VALUES(?,?)", (predecessor_attachment, case_id))
            if cancel_authority_ref:
                con.execute(
                    "UPDATE work SET status='cancelled' WHERE authority_ref=? AND status IN ('queued','running','blocked')",
                    (cancel_authority_ref,),
                )
            if work_update:
                changed = con.execute(
                    "UPDATE work SET status=? WHERE work_id=? AND authority_ref=? AND status IN ('queued','running','blocked')",
                    (work_update[1], work_update[0], work_update[2]),
                ).rowcount
                if changed != 1:
                    raise ReplayConflictError("Executor result does not match one active job")
            if case_work_status:
                if case_work_status not in {"blocked", "cancelled"}:
                    raise ValueError("invalid Case-wide work status")
                con.execute(
                    "UPDATE work SET status=? WHERE case_id=? AND status IN ('queued','running','blocked')",
                    (case_work_status, case_id),
                )
            for recorded in (*precursor_events, event):
                con.execute("INSERT INTO audit(event_type,actor_category,authority_ref,content_hash,occurred_at,outcome) VALUES(?,?,?,?,?,?)", (recorded["event_type"], audit_actor_category or recorded["actor_category"], audit_authority_ref or recorded["authority_ref"], recorded["content_hash"], recorded["occurred_at"], "accepted"))
        return "inserted"

    def case(self, case_id: str) -> dict[str, Any]:
        with self._connect() as con:
            row = con.execute("SELECT * FROM cases WHERE case_id=?", (case_id,)).fetchone()
        if not row:
            raise KeyError(case_id)
        value = json.loads(row["view_json"])
        value.update(state=row["state"], updated_at=row["updated_at"])
        return value

    def events(self, case_id: str) -> list[dict[str, Any]]:
        with self._connect() as con:
            rows = con.execute("SELECT event_json FROM events WHERE case_id=? ORDER BY rowid", (case_id,)).fetchall()
        return [json.loads(r[0]) for r in rows]

    def list_cases(self, limit: int | None = 20) -> list[dict[str, Any]]:
        with self._connect() as con:
            sql = "SELECT case_id,state,updated_at FROM cases ORDER BY updated_at DESC,rowid DESC"
            if limit is None:
                rows = con.execute(sql).fetchall()
            else:
                bounded = min(max(int(limit), 1), 50)
                rows = con.execute(sql + " LIMIT ?", (bounded,)).fetchall()
        return [dict(row) for row in rows]

    def pending_outbox(self) -> list[dict[str, Any]]:
        with self._connect() as con:
            rows = con.execute("SELECT * FROM outbox WHERE status IN ('pending','retryable') ORDER BY id").fetchall()
        return [{**dict(r), "payload": json.loads(r["payload_json"])} for r in rows]

    def store_fragment(
        self,
        *,
        sender_id: int,
        manifest: str,
        index: int,
        count: int,
        text: str,
        received_at: str,
        expires_before: str,
        max_manifests: int = 32,
    ) -> list[str] | None:
        with self._write() as con:
            con.execute("DELETE FROM fragments WHERE received_at<?", (expires_before,))
            existing = con.execute(
                "SELECT fragment_index,fragment_count,fragment_text FROM fragments WHERE sender_id=? AND manifest=? ORDER BY fragment_index",
                (sender_id, manifest),
            ).fetchall()
            if not existing:
                active = con.execute(
                    "SELECT COUNT(DISTINCT manifest) FROM fragments WHERE sender_id=?",
                    (sender_id,),
                ).fetchone()[0]
                if int(active) >= max_manifests:
                    raise ValueError("fragment manifest limit exceeded")
            else:
                if any(int(row["fragment_count"]) != count for row in existing):
                    raise ValueError("conflicting fragment count")
                prior = next((row for row in existing if int(row["fragment_index"]) == index), None)
                if prior is not None:
                    if prior["fragment_text"] != text:
                        raise ValueError("conflicting fragment replay")
                else:
                    con.execute(
                        "INSERT INTO fragments VALUES(?,?,?,?,?,?)",
                        (sender_id, manifest, index, count, text, received_at),
                    )
            if not existing:
                con.execute(
                    "INSERT INTO fragments VALUES(?,?,?,?,?,?)",
                    (sender_id, manifest, index, count, text, received_at),
                )
            rows = con.execute(
                "SELECT fragment_index,fragment_text FROM fragments WHERE sender_id=? AND manifest=? ORDER BY fragment_index",
                (sender_id, manifest),
            ).fetchall()
            if len(rows) != count or {int(row["fragment_index"]) for row in rows} != set(range(count)):
                return None
            con.execute("DELETE FROM fragments WHERE sender_id=? AND manifest=?", (sender_id, manifest))
            return [str(row["fragment_text"]) for row in rows]

    def mark_outbox(
        self,
        outbox_id: int,
        status: str,
        error: str | None = None,
        *,
        attention: Mapping[str, Any] | None = None,
    ) -> None:
        if status not in {"sent", "acknowledged", "retryable", "uncertain", "failed", "cancelled"}:
            raise ValueError("invalid outbox status")
        with self._write() as con:
            changed = con.execute(
                "UPDATE outbox SET status=?,attempts=attempts+1,last_error=? WHERE id=? AND status<>'acknowledged'",
                (status, (error or "")[:500] or None, outbox_id),
            ).rowcount
            if changed != 1 and con.execute("SELECT 1 FROM outbox WHERE id=?", (outbox_id,)).fetchone() is None:
                raise KeyError(outbox_id)
            if changed == 1 and attention is not None:
                con.execute(
                    "INSERT INTO outbox(case_id,kind,payload_json) VALUES(?,?,?)",
                    (str(attention["case_id"]), "owner_notification", json.dumps(dict(attention))),
                )

    def acknowledge_peer_event(self, event_id: str, content_hash: str) -> bool:
        with self._write() as con:
            rows = con.execute(
                "SELECT id,payload_json FROM outbox WHERE kind='peer' AND status IN ('pending','sent','retryable')"
            ).fetchall()
            matches = []
            for row in rows:
                payload = json.loads(row["payload_json"])
                if payload.get("event_id") == event_id:
                    if payload.get("content_hash") != content_hash:
                        raise ReplayConflictError("receipt hash does not match the sent Case event")
                    matches.append(int(row["id"]))
            if len(matches) != 1:
                return False
            con.execute("UPDATE outbox SET status='acknowledged' WHERE id=?", (matches[0],))
            return True

    def audit(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as con:
            rows = con.execute("SELECT event_type,actor_category,authority_ref,content_hash,occurred_at,outcome FROM audit ORDER BY id DESC LIMIT ?", (min(max(limit, 1), 100),)).fetchall()
        return [dict(r) for r in rows]

    def record_audit(self, *, event_type: str, actor_category: str, authority_ref: str, content_hash: str, occurred_at: str, outcome: str) -> None:
        with self._write() as con:
            con.execute(
                "INSERT INTO audit(event_type,actor_category,authority_ref,content_hash,occurred_at,outcome) VALUES(?,?,?,?,?,?)",
                (event_type[:128], actor_category[:32], authority_ref[:128], content_hash, occurred_at, outcome[:128]),
            )

    def revoke_grant(self, grant_id: str) -> bool:
        with self._write() as con:
            changed = con.execute("UPDATE grants SET status='revoked' WHERE grant_id=? AND status IN ('active','consumed')", (grant_id,)).rowcount
            con.execute("UPDATE work SET status='cancelled' WHERE authority_ref=? AND status IN ('queued','running','blocked')", (grant_id,))
        return changed == 1

    def revoke_work_authority(self, field: str, value: str, reason: str) -> int:
        if field not in {"owner_id", "repository_id"}:
            raise ValueError("unsupported revocation field")
        with self._write() as con:
            grant_rows = con.execute("SELECT grant_id,payload_json FROM grants WHERE status IN ('active','consumed')").fetchall()
            grant_ids = [
                str(row["grant_id"]) for row in grant_rows
                if json.loads(row["payload_json"]).get(field) == value
            ]
            for grant_id in grant_ids:
                con.execute("UPDATE grants SET status='revoked' WHERE grant_id=?", (grant_id,))
            work_rows = con.execute(
                "SELECT work_id,case_id,authority_ref,payload_json FROM work WHERE status IN ('queued','running','blocked')"
            ).fetchall()
            targets = [
                row for row in work_rows
                if row["authority_ref"] in grant_ids or json.loads(row["payload_json"]).get(field) == value
            ]
            for row in targets:
                con.execute("UPDATE work SET status='cancelled' WHERE work_id=?", (row["work_id"],))
            for case_id in sorted({str(row["case_id"]) for row in targets}):
                con.execute(
                    "INSERT INTO outbox(case_id,kind,payload_json) VALUES(?,?,?)",
                    (case_id, "owner_notification", json.dumps({"case_id": case_id, "reason": reason})),
                )
        return len(targets)

    def enqueue_work(self, item: Mapping[str, Any]) -> None:
        with self._write() as con:
            con.execute("INSERT OR IGNORE INTO work VALUES(?,?,?,?,?,'queued',NULL,?,?)", (item["work_id"], item["case_id"], item["capability"], item["executor"], json.dumps(item["payload"]), item["authority_ref"], item.get("candidate_revision")))

    def work_items(self, case_id: str) -> list[dict[str, Any]]:
        with self._connect() as con:
            rows = con.execute("SELECT * FROM work WHERE case_id=? ORDER BY rowid", (case_id,)).fetchall()
        values = []
        for row in rows:
            value = dict(row)
            value["payload"] = json.loads(value.pop("payload_json"))
            values.append(value)
        return values

    def next_work(self, capabilities: Sequence[str]) -> dict[str, Any] | None:
        if not capabilities:
            return None
        with self._connect() as con:
            marks = ",".join("?" for _ in capabilities)
            row = con.execute(
                f"SELECT * FROM work WHERE status='queued' AND capability IN ({marks}) ORDER BY rowid LIMIT 1",
                tuple(capabilities),
            ).fetchone()
        if not row:
            return None
        value = dict(row)
        value["payload"] = json.loads(value.pop("payload_json"))
        return value

    def policy(self, policy_id: str) -> dict[str, Any] | None:
        with self._connect() as con:
            row = con.execute("SELECT payload_json,status,revision FROM policies WHERE policy_id=?", (policy_id,)).fetchone()
        if not row:
            return None
        value = json.loads(row["payload_json"])
        value.update(status=row["status"], revision=row["revision"])
        return value

    def active_policy(self, repository_id: str, intake_agent_id: str) -> dict[str, Any] | None:
        with self._connect() as con:
            rows = con.execute("SELECT payload_json,status,revision FROM policies WHERE status='active'").fetchall()
        matches = []
        for row in rows:
            value = json.loads(row["payload_json"])
            if value.get("repository_id") == repository_id and value.get("intake_agent_id") == intake_agent_id:
                value.update(status=row["status"], revision=row["revision"])
                matches.append(value)
        if len(matches) > 1:
            raise ReplayConflictError("multiple active StandingAnalysisPolicy bindings")
        return matches[0] if matches else None

    def grant_status(self, grant_id: str) -> str | None:
        with self._connect() as con:
            row = con.execute("SELECT status FROM grants WHERE grant_id=?", (grant_id,)).fetchone()
        return str(row["status"]) if row else None

    def question(self, question_id: str) -> dict[str, Any] | None:
        with self._connect() as con:
            row = con.execute("SELECT * FROM questions WHERE question_id=?", (question_id,)).fetchone()
        if not row:
            return None
        value = dict(row)
        value["payload"] = json.loads(value.pop("payload_json"))
        return value

    def open_question(self, case_id: str) -> dict[str, Any] | None:
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM questions WHERE case_id=? AND status='open' ORDER BY rowid DESC LIMIT 1", (case_id,),
            ).fetchone()
        if not row:
            return None
        value = dict(row)
        value["payload"] = json.loads(value.pop("payload_json"))
        return value

    def current_candidate(self, case_id: str) -> dict[str, Any] | None:
        with self._connect() as con:
            row = con.execute("SELECT payload_json,status FROM candidates WHERE case_id=? AND status='current'", (case_id,)).fetchone()
        if not row:
            return None
        value = json.loads(row["payload_json"])
        value["status"] = row["status"]
        return value

    def review(self, review_id: str) -> dict[str, Any] | None:
        with self._connect() as con:
            row = con.execute("SELECT payload_json,status FROM reviews WHERE review_id=?", (review_id,)).fetchone()
        if not row:
            return None
        value = json.loads(row["payload_json"])
        value["status"] = row["status"]
        return value

    def current_review(self, case_id: str) -> dict[str, Any] | None:
        with self._connect() as con:
            row = con.execute("SELECT payload_json,status FROM reviews WHERE case_id=? AND status='current'", (case_id,)).fetchone()
        if not row:
            return None
        value = json.loads(row["payload_json"])
        value["status"] = row["status"]
        return value

    def accepted_findings(self, case_id: str) -> dict[str, dict[str, Any]]:
        with self._connect() as con:
            rows = con.execute("SELECT finding_id,payload_json FROM findings WHERE case_id=? AND accepted=1", (case_id,)).fetchall()
        return {row["finding_id"]: json.loads(row["payload_json"]) for row in rows}

    def current_merge_decision(self, case_id: str) -> dict[str, Any] | None:
        with self._connect() as con:
            row = con.execute("SELECT payload_json,status FROM merge_decisions WHERE case_id=? AND status='current' ORDER BY rowid DESC LIMIT 1", (case_id,)).fetchone()
        if not row:
            return None
        value = json.loads(row["payload_json"])
        value["status"] = row["status"]
        return value

    def discussion(self, mandate_id: str) -> dict[str, Any] | None:
        with self._connect() as con:
            row = con.execute("SELECT payload_json,status,turn_count,cost_used FROM discussions WHERE mandate_id=?", (mandate_id,)).fetchone()
        if not row:
            return None
        value = json.loads(row["payload_json"])
        value.update(status=row["status"], turn_count=row["turn_count"], cost_used=row["cost_used"])
        return value

    def claim_work(self, worker: str, capabilities: Sequence[str]) -> dict[str, Any] | None:
        if not capabilities:
            return None
        with self._write() as con:
            marks = ",".join("?" for _ in capabilities)
            row = con.execute(f"SELECT * FROM work WHERE status='queued' AND capability IN ({marks}) ORDER BY rowid LIMIT 1", tuple(capabilities)).fetchone()
            if not row:
                return None
            payload = json.loads(row["payload_json"])
            expires_at = payload.get("expires_at")
            if expires_at:
                expiry = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
                if expiry <= datetime.now(timezone.utc):
                    con.execute("UPDATE work SET status='expired' WHERE work_id=?", (row["work_id"],))
                    if payload.get("grant_id"):
                        con.execute("UPDATE grants SET status='expired' WHERE grant_id=? AND status='active'", (payload["grant_id"],))
                    return None
            changed = con.execute("UPDATE work SET status='running',lease_owner=? WHERE work_id=? AND status='queued'", (worker, row["work_id"])).rowcount
            if changed != 1:
                return None
            if payload.get("grant_id"):
                if row["lease_owner"] is None:
                    consumed = con.execute("UPDATE grants SET status='consumed' WHERE grant_id=? AND status='active'", (payload["grant_id"],)).rowcount
                    if consumed != 1:
                        raise ReplayConflictError("Execution Grant is not active")
                else:
                    grant = con.execute("SELECT status FROM grants WHERE grant_id=?", (payload["grant_id"],)).fetchone()
                    if not grant or grant["status"] != "consumed":
                        raise ReplayConflictError("Execution Grant cannot resume this job")
        value = dict(row); value["payload"] = json.loads(value.pop("payload_json")); return value

    def complete_work(self, work_id: str, status: str) -> None:
        with self._write() as con:
            changed = con.execute("UPDATE work SET status=? WHERE work_id=? AND status='running'", (status, work_id)).rowcount
            if changed != 1:
                raise ReplayConflictError("work is not actively leased")

    def requeue_work(self, work_id: str, worker: str) -> None:
        with self._write() as con:
            row = con.execute(
                "SELECT payload_json FROM work WHERE work_id=? AND status='running' AND lease_owner=?",
                (work_id, worker),
            ).fetchone()
            if row is None:
                raise ReplayConflictError("work is not leased by this worker")
            payload = json.loads(row["payload_json"])
            grant_id = payload.get("grant_id")
            if grant_id:
                restored = con.execute(
                    "UPDATE grants SET status='active' WHERE grant_id=? AND status='consumed'",
                    (grant_id,),
                ).rowcount
                if restored != 1:
                    raise ReplayConflictError("Execution Grant cannot be restored for requeue")
            con.execute(
                "UPDATE work SET status='queued',lease_owner=NULL WHERE work_id=?",
                (work_id,),
            )

    def recover_interrupted_work(self) -> int:
        """Atomically make process-abandoned leases reclaimable after restart."""
        with self._write() as con:
            rows = con.execute(
                "SELECT work_id,payload_json FROM work WHERE status='running' AND lease_owner IS NOT NULL"
            ).fetchall()
            for row in rows:
                grant_id = json.loads(row["payload_json"]).get("grant_id")
                if grant_id:
                    restored = con.execute(
                        "UPDATE grants SET status='active' WHERE grant_id=? AND status='consumed'",
                        (grant_id,),
                    ).rowcount
                    if restored != 1:
                        raise ReplayConflictError("Execution Grant cannot be recovered")
                con.execute(
                    "UPDATE work SET status='queued',lease_owner=NULL WHERE work_id=?",
                    (row["work_id"],),
                )
            return len(rows)

    def cancel_claimed_work(self, work_id: str, authority_ref: str, case_id: str, reason: str) -> None:
        with self._write() as con:
            changed = con.execute(
                "UPDATE work SET status='cancelled' WHERE work_id=? AND status='running'",
                (work_id,),
            ).rowcount
            if changed != 1:
                raise ReplayConflictError("claimed work is not cancellable")
            con.execute(
                "UPDATE grants SET status='revoked' WHERE grant_id=? AND status IN ('active','consumed')",
                (authority_ref,),
            )
            con.execute(
                "INSERT INTO outbox(case_id,kind,payload_json) VALUES(?,?,?)",
                (case_id, "owner_notification", json.dumps({"case_id": case_id, "reason": reason})),
            )
