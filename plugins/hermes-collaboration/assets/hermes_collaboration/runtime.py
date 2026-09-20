from __future__ import annotations

import asyncio
import os
import hashlib
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from .adapters.peer_telegram import PeerCodec, PeerDeliveryError, PeerTelegramAdapter, PeerTrustInactiveError, TelegramTransport
from .adapters.executors import ExecutorAdapter, SafetyViolation
from .adapters.github_delivery import ExternalActionUncertain, GitHubDelivery
from .adapters.github_webhook import GitHubWebhook
from .adapters.owner_telegram import OwnerTelegramAdapter
from .cases.coordinator import CaseCoordinator
from .cases.contracts import content_hash, utc_now
from .cases.errors import AuthorizationError, CollaborationError
from .config import CollaborationConfig, default_config_path, load_config
from .storage.sqlite_case_store import ReplayConflictError, SQLiteCaseStore


class CollaborationRuntime:
    MAX_OUTBOX_ATTEMPTS = 5
    submit_schema = {"type": "object", "additionalProperties": True}
    view_schema = {"type": "object", "required": ["case_id"], "properties": {"case_id": {"type": "string"}}}
    list_schema = {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 50}}}
    github_event_schema = {"type": "object", "required": ["body", "signature", "delivery_id"]}
    run_work_schema = {"type": "object", "properties": {"worker": {"type": "string"}}}

    def __init__(self, coordinator: CaseCoordinator, *, config: CollaborationConfig, transport: Any, github_webhook: Any | None = None, github_client: Any | None = None, executor_runners: Mapping[str, Any] | None = None, repository_paths: Mapping[str, str] | None = None) -> None:
        self.coordinator = coordinator
        self.config = config
        self.transport = transport
        self.codec = PeerCodec(config.max_message_bytes)
        self.peer_adapter = PeerTelegramAdapter(
            self.codec,
            transport,
            coordinator,
            config.representative_id,
            {
                peer_id: {
                    "username": peer.telegram_username,
                    "numeric_bot_id": peer.numeric_bot_id,
                    "revision": peer.revision,
                    "active": peer.active,
                }
                for peer_id, peer in config.peers.items()
            },
            min_send_interval_seconds=config.min_send_interval_seconds,
        )
        self.owner_adapter = OwnerTelegramAdapter({config.owner_chat_id: config.owner_ids[0]})
        self.github_webhook = github_webhook
        self.github_client = github_client
        self.executor_runners = dict(executor_runners or {})
        self.repository_paths = dict(repository_paths or {})
        self._tasks: set[asyncio.Task[Any]] = set()
        self._flush_lock = asyncio.Lock()

    @classmethod
    def from_profile(cls, home: Path, llm: Any = None, *, github_webhook_secret: bytes | None = None, github_allowed_actors: tuple[str, ...] = (), github_machine_login: str | None = None, github_allowed_labels: tuple[str, ...] = (), github_client: Any | None = None, executor_runners: Mapping[str, Any] | None = None, repository_paths: Mapping[str, str] | None = None) -> "CollaborationRuntime":
        del llm
        config = load_config(default_config_path(home))
        peer_trust = {peer_id: peer.revision for peer_id, peer in config.peers.items() if peer.active}
        store = SQLiteCaseStore(home / "state" / "hermes-collaboration" / "cases.sqlite3")
        store.recover_interrupted_work()
        coordinator = CaseCoordinator(
            store,
            config.representative_id,
            peer_trust=peer_trust,
            executor_registrations=config.executor_registrations,
            owner_ids=config.owner_ids,
            intake_agents=config.intake_agents,
            authorized_repositories=config.authorized_repositories,
        )
        webhook = None
        if github_webhook_secret:
            webhook = GitHubWebhook(
                github_webhook_secret, allowed_repositories=config.authorized_repositories,
                allowed_actors=github_allowed_actors,
                allowed_intake_agents=set(config.intake_agents) & set(github_allowed_actors),
                machine_login=github_machine_login,
                allowed_labels=github_allowed_labels,
            )
        return cls(
            coordinator, config=config, transport=TelegramTransport(os.getenv("TELEGRAM_BOT_TOKEN", "")),
            github_webhook=webhook, github_client=github_client,
            executor_runners=executor_runners, repository_paths=repository_paths,
        )

    def submit_tool(self, args: Mapping[str, Any] | None = None, **command: Any) -> dict[str, Any]:
        values = dict(args or command)
        actor = values.get("actor", {})
        if actor.get("category") != "representative" or actor.get("actor_ref") != self.config.representative_id or actor.get("representative_id") != self.config.representative_id:
            raise AuthorizationError("case_submit requires the local Representative identity")
        decision = self.coordinator.submit(values)
        return {
            "status": decision.status,
            "case_id": decision.case_id,
            "event_ids": list(decision.event_ids),
            "work_id": decision.work_id,
        }

    def view_tool(self, args: Mapping[str, Any] | None = None, **values: Any) -> dict[str, Any]:
        request = dict(args or values)
        return self.coordinator.view(
            str(request["case_id"]),
            {"category": "owner", "actor_ref": self.config.owner_ids[0]},
            str(request.get("view_kind", "summary")),
            request.get("cursor"),
        )

    def list_tool(self, args: Mapping[str, Any] | None = None, **values: Any) -> dict[str, Any]:
        request = dict(args or values)
        limit = min(max(int(request.get("limit", 20)), 1), 50)
        return {"cases": self.coordinator.store.list_cases(limit), "limit": limit}

    def github_event_tool(self, args: Mapping[str, Any] | None = None, **values: Any) -> dict[str, Any]:
        if self.github_webhook is None:
            raise AuthorizationError("GitHub webhook ingress is not configured")
        request = dict(args or values)
        admitted = self.github_webhook.admit(
            str(request["body"]).encode(), str(request["signature"]), str(request["delivery_id"]),
        )
        if admitted.get("duplicate"):
            return {"status": "duplicate", "delivery_id": admitted["delivery_id"]}
        case_id = "case:" + hashlib.sha256(str(admitted["source_trace_id"]).encode()).hexdigest()[:24]
        policy = self.coordinator.store.active_policy(str(admitted["repository_id"]), str(admitted["actor"])) if admitted["trigger"] == "intake_creation" else None
        actor = {"category": "github", "actor_ref": "github:webhook", "representative_id": self.config.representative_id}
        authority = {"authority_type": "system_admission", "authority_ref": f"github:{admitted['delivery_id']}", "revision": 1}
        if policy is not None:
            actor = {"category": "intake_agent", "actor_ref": admitted["actor"], "representative_id": self.config.representative_id}
            authority = {"authority_type": "standing_analysis_policy", "authority_ref": policy["policy_id"], "revision": policy["revision"]}
        command = {
            "command_id": f"github:{admitted['delivery_id']}", "command_type": "admit_issue",
            "case_id": case_id, "source_trace_id": admitted["source_trace_id"],
            "actor": actor, "authority": authority,
            "issued_at": "1970-01-01T00:00:00Z",
            "payload": {
                **admitted, "repository_visibility": "private",
                "issue_ref": admitted["issue_identity"], "executor": "codex",
            },
        }
        decision = self.coordinator.submit_ingress(command, ingress="github_webhook")
        return {"status": decision.status, "case_id": case_id, "delivery_id": admitted["delivery_id"]}

    def run_work_tool(self, args: Mapping[str, Any] | None = None, **values: Any) -> dict[str, Any]:
        request = dict(args or values)
        worker = str(request.get("worker") or f"runtime:{self.config.representative_id}")
        capabilities = ("issue_analysis", "reanalysis", "pr_review", "implementation", "correction")
        pending = self.coordinator.store.next_work(capabilities)
        if pending is None:
            return {"status": "idle"}
        pending_payload = pending["payload"]
        pending_repository = str(pending_payload.get("repository_id") or "")
        if pending["executor"] not in self.executor_runners or pending_repository not in self.repository_paths:
            return {"status": "queued", "reason": "executor_or_repository_offline"}
        if pending["capability"] in {"implementation", "correction"}:
            reviewer = next((name for name in self.executor_runners if name != pending["executor"]), None)
            if reviewer is None or self.github_client is None:
                return {"status": "queued", "reason": "reviewer_or_github_offline"}
        work = self.coordinator.claim_work(worker, capabilities)
        if work is None:
            return {"status": "idle"}
        runner = self.executor_runners.get(work.executor)
        repository_id = str(work.payload.get("repository_id") or "")
        repository_path = self.repository_paths.get(repository_id)
        reviewer = next((name for name in self.executor_runners if name != work.executor), None)
        pipeline_unavailable = (
            runner is None or not repository_path
            or (work.capability in {"implementation", "correction"} and (reviewer is None or self.github_client is None))
        )
        if pipeline_unavailable:
            self.coordinator.store.requeue_work(work.work_id, worker)
            return {"status": "queued", "reason": "executor_pipeline_changed"}
        try:
            outcome = ExecutorAdapter(work.executor, runner).run({
                **dict(work.payload), "job_id": work.work_id, "case_id": work.case_id,
                "capability": work.capability, "repository_path": repository_path,
                "authority_ref": str(work.payload.get("grant_id") or work.payload.get("_authority_ref") or "owner-decision"),
            })
        except Exception:
            self.coordinator.store.complete_work(work.work_id, "failed")
            return {"status": "failed", "case_id": work.case_id, "work_id": work.work_id, "reason": "executor_failed"}
        status = str(outcome.get("status"))
        if status == "success":
            status = "completed"
        if status not in {"blocked", "uncertain", "failed", "review_ready", "completed", "cancelled"}:
            self.coordinator.store.complete_work(work.work_id, "failed")
            return {"status": "failed", "case_id": work.case_id, "work_id": work.work_id, "reason": "invalid_executor_result"}
        if work.capability == "pr_review":
            candidate_revision = str(work.payload.get("candidate_revision") or "")
            verify_candidate = getattr(self.github_client, "verify_candidate", None)
            if not callable(verify_candidate):
                self.coordinator.store.complete_work(work.work_id, "failed")
                return {"status": "failed", "case_id": work.case_id, "work_id": work.work_id, "reason": "github_candidate_verification_unavailable"}
            github_evidence = verify_candidate(repository_id, candidate_revision)
            github_verified = (
                isinstance(github_evidence, Mapping)
                and github_evidence.get("candidate_revision") == candidate_revision
                and github_evidence.get("checks_passed") is True
                and github_evidence.get("repository_current") is True
            )
            evidence_names = {
                "complete_diff": "complete_diff", "repository_standards": "repository_standards",
                "approved_scope": "approved_scope", "security_constraints": "security_constraints",
                "fresh_verification": "fresh_verification",
            }
            review = {
                "review_id": str(outcome.get("review_id") or f"review:{work.work_id}"),
                "case_id": work.case_id, "candidate_revision": candidate_revision,
                "reviewer_registration_ref": str(work.payload["executor_registration_ref"]),
                "reviewer_job_id": work.work_id,
                "reviewer_session_id": str(outcome.get("reviewer_session_id") or f"session:{work.work_id}"),
                "verdict": str(outcome.get("verdict") or "fail"),
                "inputs": [name for field, name in evidence_names.items() if work.payload.get(field) not in (None, "", [])],
                "findings": list(outcome.get("findings") or []),
                "checks_passed": github_verified,
                "repository_current": github_verified,
                "completed_at": utc_now(),
            }
            try:
                self.coordinator.record_review(review)
            except CollaborationError:
                self.coordinator.store.complete_work(work.work_id, "failed")
                return {"status": "failed", "case_id": work.case_id, "work_id": work.work_id, "reason": "invalid_review_result"}
            self.coordinator.store.complete_work(work.work_id, "completed")
            return {"status": "completed", "case_id": work.case_id, "work_id": work.work_id, "review_id": review["review_id"]}
        result_payload = {**outcome, "status": status, "job_id": work.work_id}
        if status == "review_ready":
            evidence_fields = {
                "base_revision", "candidate_revision", "changed_scope", "verification",
                "complete_diff", "repository_standards", "security_constraints", "draft_pr",
            }
            if any(outcome.get(field) in (None, "", []) for field in evidence_fields):
                result_payload["status"] = status = "failed"
            elif any(
                not isinstance(item, Mapping)
                or not isinstance(item.get("command"), list)
                or item.get("exit_code") != 0
                for item in outcome["verification"]
            ):
                result_payload["status"] = status = "failed"
        if status == "blocked" and isinstance(outcome.get("question"), Mapping):
            question = dict(outcome["question"])
            try:
                self.coordinator.record_question({
                    "question_id": str(question["question_id"]), "case_id": work.case_id,
                    "job_id": work.work_id, "grant_id": str(work.payload.get("grant_id") or work.payload.get("_authority_ref")),
                    "asked_by": str(work.payload.get("executor_registration_ref") or next(iter(self.config.executor_registrations))),
                    "responsible_owner": self.config.owner_ids[0], "question": str(question["question"]),
                    "blocker": str(question["blocker"]), "next_action": str(question["next_action"]),
                    "occurred_at": utc_now(),
                })
            except (CollaborationError, KeyError, ValueError):
                self.coordinator.store.complete_work(work.work_id, "failed")
                return {"status": "failed", "case_id": work.case_id, "work_id": work.work_id, "reason": "invalid_question"}
            return {"status": "blocked", "case_id": work.case_id, "work_id": work.work_id}
        draft = outcome.get("draft_pr")
        try:
            if status == "review_ready" and isinstance(draft, Mapping):
                delivery = GitHubDelivery(self.github_client, store=self.coordinator.store, case_id=work.case_id)
                delivery.publish_draft({
                    "repository": repository_id, "repository_visibility": "private",
                    "source_trace_id": str(work.payload.get("source_trace_id") or f"case:{work.case_id}"),
                    "candidate_revision": outcome["candidate_revision"], "base_revision": outcome["base_revision"],
                    "branch": draft["branch"], "commit": draft["commit"], "title": draft["title"], "body": draft["body"],
                })
        except ExternalActionUncertain:
            self.coordinator.store.complete_work(work.work_id, "uncertain")
            return {"status": "uncertain", "case_id": work.case_id, "work_id": work.work_id, "reason": "github_delivery_uncertain"}
        except (KeyError, PermissionError, ValueError):
            self.coordinator.store.complete_work(work.work_id, "failed")
            return {"status": "failed", "case_id": work.case_id, "work_id": work.work_id, "reason": "github_delivery_failed"}
        executor_ref = str(work.payload.get("executor_registration_ref") or next(iter(self.config.executor_registrations)))
        try:
            decision = self.coordinator.submit({
                "command_id": f"result:{work.work_id}:{content_hash(result_payload)[7:19]}",
                "command_type": "submit_executor_result", "case_id": work.case_id,
                "actor": {"category": "executor", "actor_ref": executor_ref, "representative_id": self.config.representative_id, "registration_revision": self.config.executor_registrations[executor_ref]},
                "authority": {"authority_type": "execution_grant", "authority_ref": str(work.payload.get("grant_id") or work.payload.get("_authority_ref") or "owner-decision"), "revision": 1},
                "issued_at": utc_now(), "payload": result_payload,
            })
        except CollaborationError:
            self.coordinator.store.complete_work(work.work_id, "failed")
            return {"status": "failed", "case_id": work.case_id, "work_id": work.work_id, "reason": "invalid_executor_result"}
        if status == "review_ready" and isinstance(draft, Mapping):
            reviewer_registration = next(
                (ref for ref in self.config.executor_registrations if ref != work.payload.get("executor_registration_ref")),
                next(iter(self.config.executor_registrations)),
            )
            if reviewer is None:
                raise SafetyViolation("independent review Executor is unavailable")
            self.coordinator.store.enqueue_work({
                "work_id": f"review:{outcome['candidate_revision']}", "case_id": work.case_id,
                "capability": "pr_review", "executor": reviewer,
                "authority_ref": f"candidate:{outcome['candidate_revision']}",
                "candidate_revision": outcome["candidate_revision"],
                "payload": {
                    "repository_id": repository_id, "candidate_revision": outcome["candidate_revision"],
                    "executor_registration_ref": reviewer_registration,
                    "complete_diff": outcome["complete_diff"],
                    "repository_standards": outcome["repository_standards"],
                    "security_constraints": outcome["security_constraints"],
                    "approved_scope": list(work.payload.get("scope") or []),
                    "fresh_verification": list(outcome.get("verification") or []),
                    "prohibited_actions": ["edit", "write", "push", "merge", "release", "deploy"],
                },
            })
        return {"status": status, "case_id": decision.case_id, "work_id": work.work_id}

    def pre_gateway_dispatch(self, *, event: Any, gateway: Any, **_: Any) -> dict[str, str] | None:
        if self._platform_name(event) != "telegram":
            return None
        self._schedule(self.flush_outbox(gateway))
        raw_user = getattr(getattr(event, "raw_message", None), "from_user", None)
        if not bool(getattr(raw_user, "is_bot", False)):
            chat_id = int(getattr(getattr(event, "source", None), "chat_id", 0) or 0)
            sender_id = str(getattr(raw_user, "id", ""))
            if chat_id != self.config.owner_chat_id or sender_id not in self.config.owner_ids:
                return None
            text = str(getattr(event, "text", "") or "")
            normalized = text.lower().lstrip("/")
            if not normalized.startswith(("approve ", "approve:", "reject ", "reject:", "defer ", "defer:", "narrow ", "narrow:", "clarify ", "clarify:", "pause ", "pause:", "stop ", "stop:", "answer ", "answer:")):
                return None
            message_id = int(getattr(getattr(event, "raw_message", None), "message_id", 0) or 0)
            try:
                lifecycle = self._owner_lifecycle(text, chat_id=chat_id, message_id=message_id)
                if lifecycle is not None:
                    return {"action": "skip", "reason": lifecycle}
                command = self.owner_adapter.resolve_input(
                    text, self._owner_targets(), chat_id=chat_id, message_id=message_id,
                    input_kind="command" if text.startswith("/") else "natural_language",
                )
                command["actor"]["representative_id"] = self.config.representative_id
                self.coordinator.submit(command)
                return {"action": "skip", "reason": "collaboration-owner-decision-recorded"}
            except (CollaborationError, ReplayConflictError, PermissionError, ValueError, sqlite3.Error, StopIteration):
                return {"action": "skip", "reason": "collaboration-owner-clarification-required"}
        text = str(getattr(event, "text", "") or "")
        if not text.startswith("HERMES_CASE_V1\n"):
            return None
        sender_id = getattr(raw_user, "id", None)
        if not isinstance(sender_id, int) or not any(peer.active and peer.numeric_bot_id == sender_id for peer in self.config.peers.values()):
            return {"action": "skip", "reason": "collaboration-peer-rejected"}
        try:
            parts = self._collect_fragment(sender_id, text)
            if parts is None:
                return {"action": "skip", "reason": "collaboration-fragment-buffered"}
            delivery = self.codec.reassemble(parts, now=datetime.now(timezone.utc))
            peer = self.config.peers.get(str(delivery.get("sender_representative_id")))
            if peer is None or not peer.active or peer.numeric_bot_id != sender_id:
                raise PeerDeliveryError("exact bot identity not trusted")
            if delivery.get("recipient_representative_id") != self.config.representative_id:
                raise PeerDeliveryError("recipient identity mismatch")
            if delivery.get("delivery_type") == "case_event":
                if delivery.get("content_hash") != delivery.get("payload", {}).get("content_hash"):
                    raise PeerDeliveryError("Case event hash binding mismatch")
                self.coordinator.ingest_peer(delivery["payload"], peer.representative_id, delivery_id=str(delivery["delivery_id"]))
            elif delivery.get("delivery_type") == "receipt":
                payload = delivery.get("payload", {})
                if delivery.get("content_hash") != payload.get("content_hash"):
                    raise PeerDeliveryError("receipt hash binding mismatch")
                if not self.coordinator.store.acknowledge_peer_event(str(payload.get("event_id")), str(payload.get("content_hash"))):
                    raise PeerDeliveryError("receipt does not match one sent Case event")
            else:
                raise PeerDeliveryError("unsupported delivery type")
            self._schedule(self.flush_outbox(gateway))
            return {"action": "skip", "reason": "collaboration-peer-ingested"}
        except (PeerDeliveryError, CollaborationError, ReplayConflictError, KeyError, ValueError, sqlite3.Error) as exc:
            self.coordinator.store.record_audit(
                event_type="peer_delivery_rejected",
                actor_category="peer",
                authority_ref=f"telegram-bot:{sender_id}",
                content_hash=content_hash({"sender_id": sender_id, "envelope": text}),
                occurred_at=utc_now(),
                outcome=f"denied:{type(exc).__name__}",
            )
            return {"action": "skip", "reason": "collaboration-peer-rejected"}

    async def flush_outbox(self, gateway: Any) -> dict[str, int]:
        async with self._flush_lock:
            return await self._flush_outbox_once(gateway)

    async def _flush_outbox_once(self, gateway: Any) -> dict[str, int]:
        counts = {"sent": 0, "retryable": 0, "uncertain": 0, "failed": 0, "cancelled": 0}
        for item in self.coordinator.store.pending_outbox():
            try:
                if item["kind"] in {"peer", "receipt"}:
                    event = item["payload"]
                    peer_id = self._recipient_for(event)
                    created_at = str(event.get("occurred_at") or datetime.now(timezone.utc).isoformat())
                    expires_at = str(event.get("payload", {}).get("expires_at") or (datetime.fromisoformat(created_at.replace("Z", "+00:00")) + timedelta(seconds=self.config.ttl_seconds)).isoformat())
                    is_receipt = item["kind"] == "receipt"
                    content_hash = str(event["content_hash"])
                    delivery = {
                        "delivery_id": f"{'receipt' if is_receipt else 'delivery'}:{self.config.representative_id}:{item['id']}",
                        "delivery_type": "receipt" if is_receipt else "case_event",
                        "sender_representative_id": self.config.representative_id,
                        "recipient_representative_id": peer_id,
                        "case_id": event.get("case_id", item["case_id"]),
                        "created_at": created_at,
                        "expires_at": expires_at,
                        "content_hash": content_hash,
                        "payload": event,
                    }
                    results = await self.peer_adapter.send(peer_id, delivery)
                    if all(result.success for result in results):
                        self.coordinator.store.mark_outbox(item["id"], "sent")
                        counts["sent"] += 1
                    elif any(result.retryable for result in results):
                        error = next((result.error_code for result in results if not result.success), "transport_error")
                        if error == "timeout":
                            self._finish_outbox(item, "uncertain", error, counts, notify_owner=True)
                        elif int(item["attempts"]) + 1 >= self.MAX_OUTBOX_ATTEMPTS:
                            self._finish_outbox(item, "failed", error, counts, notify_owner=True)
                        else:
                            self._finish_outbox(item, "retryable", error, counts)
                    else:
                        error = next((result.error_code for result in results if not result.success), "transport_error")
                        self._finish_outbox(item, "failed", error, counts, notify_owner=True)
                elif item["kind"] == "owner_notification":
                    adapter = self._telegram_adapter(gateway)
                    payload = item["payload"]
                    case_id = str(payload.get("case_id", item["case_id"]))
                    summary = self.coordinator.view(case_id, {"category": "owner", "actor_ref": self.config.owner_ids[0]}, "summary")
                    text = self._owner_text(summary, payload)
                    result = await adapter.send(str(self.config.owner_chat_id), text, metadata={}) if adapter is not None else await self.transport.send(str(self.config.owner_chat_id), text)
                    status = "sent" if getattr(result, "success", False) else ("failed" if int(item["attempts"]) + 1 >= self.MAX_OUTBOX_ATTEMPTS else "retryable")
                    self._finish_outbox(item, status, None if status == "sent" else "owner_notification_failed", counts)
                else:
                    self.coordinator.store.mark_outbox(item["id"], "failed", "unsupported_outbox_kind")
                    counts["failed"] += 1
            except PeerTrustInactiveError as exc:
                self._finish_outbox(
                    item, "cancelled", type(exc).__name__, counts,
                    notify_owner=item["kind"] != "owner_notification",
                    attention_reason="peer_trust_revoked",
                )
            except (PeerDeliveryError, KeyError, ValueError) as exc:
                self._finish_outbox(item, "failed", type(exc).__name__, counts, notify_owner=item["kind"] != "owner_notification")
            except Exception as exc:
                self._finish_outbox(item, "uncertain", type(exc).__name__, counts, notify_owner=item["kind"] != "owner_notification")
        return counts

    def _finish_outbox(
        self,
        item: Mapping[str, Any],
        status: str,
        error: str | None,
        counts: dict[str, int],
        *,
        notify_owner: bool = False,
        attention_reason: str | None = None,
    ) -> None:
        attention = None
        if notify_owner:
            attention = {
                "case_id": item["case_id"],
                "reason": attention_reason or ("delivery_uncertain" if status == "uncertain" else "delivery_failed"),
                "outbox_id": item["id"],
                "error": error,
            }
        self.coordinator.store.mark_outbox(item["id"], status, error, attention=attention)
        counts[status] += 1

    async def drain_background_tasks(self) -> None:
        while self._tasks:
            await asyncio.gather(*tuple(self._tasks), return_exceptions=True)

    def pre_tool_call(self, tool_name: str, **values: Any) -> dict[str, str] | None:
        normalized = str(tool_name).lower().replace("-", "_")
        arguments = str(values.get("tool_input") or values.get("arguments") or "").lower()
        commitment_tokens = {"merge", "deploy", "release"}
        if any(part in normalized for part in commitment_tokens) or any(part in arguments for part in commitment_tokens):
            return {"action": "block", "message": "Hermes v0.1 does not execute merge, deploy, or release; the Owner must perform an authorized merge manually in GitHub"}
        return None

    def post_tool_call(self, tool_name: str, **_: Any) -> None:
        if tool_name in {"case_submit", "case_github_event", "case_run_work"}:
            coroutine = self.flush_outbox(None)
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                asyncio.run(coroutine)
            else:
                self._schedule(coroutine)

    def _collect_fragment(self, sender_id: int, text: str) -> list[str] | None:
        import json

        wrapper = json.loads(text[len("HERMES_CASE_V1\n"):])
        if wrapper.get("kind") != "fragment":
            return [text]
        manifest = str(wrapper.get("manifest"))
        index, count = int(wrapper["index"]), int(wrapper["count"])
        if count < 1 or count > 100 or index < 0 or index >= count:
            raise PeerDeliveryError("invalid fragment bounds")
        now = datetime.now(timezone.utc)
        return self.coordinator.store.store_fragment(
            sender_id=sender_id,
            manifest=manifest,
            index=index,
            count=count,
            text=text,
            received_at=now.isoformat(),
            expires_before=(now - timedelta(seconds=self.config.ttl_seconds)).isoformat(),
        )

    def _owner_lifecycle(self, text: str, *, chat_id: int, message_id: int) -> str | None:
        normalized_text = text.lower().lstrip("/")
        case_id = next(
            (str(row["case_id"]) for row in self.coordinator.store.list_cases(None) if str(row["case_id"]).lower() in normalized_text),
            None,
        )
        if case_id is None:
            return None
        actor = {"category": "owner", "actor_ref": self.config.owner_ids[0], "representative_id": self.config.representative_id}
        question = self.coordinator.store.open_question(case_id)
        if normalized_text.startswith(("answer ", "answer:")) and question:
            answer_text = text[text.lower().find(case_id.lower()) + len(case_id):].strip()
            if not answer_text:
                return "collaboration-owner-clarification-required"
            self.coordinator.answer_question({
                "answer_id": f"telegram:{chat_id}:{message_id}", "question_id": question["question_id"],
                "case_id": case_id, "answer": answer_text, "occurred_at": utc_now(),
            }, actor)
            return "collaboration-owner-answer-recorded"
        if not normalized_text.startswith(("approve ", "approve:")):
            return None
        review = self.coordinator.store.current_review(case_id)
        candidate = self.coordinator.store.current_candidate(case_id)
        if not review or not candidate:
            return None
        if review.get("findings"):
            if any(item["work_id"] == f"job:correction:{review['review_id']}" for item in self.coordinator.store.work_items(case_id)):
                return "collaboration-correction-grant-recorded"
            finding_ids = [str(item["finding_id"]) for item in review["findings"]]
            self.coordinator.accept_findings(case_id, finding_ids, actor)
            scope = sorted({path for item in review["findings"] for path in item["affected_scope"]})
            repository_id = str(next(event["payload"]["repository_id"] for event in reversed(self.coordinator.store.events(case_id)) if event["event_type"] == "issue_linked"))
            source_trace_id = str(next(event.get("source_trace_id") for event in reversed(self.coordinator.store.events(case_id)) if event["event_type"] == "issue_linked"))
            command = {
                "command_id": f"telegram:{chat_id}:{message_id}:correction", "command_type": "record_owner_decision", "case_id": case_id,
                "actor": actor, "authority": {"authority_type": "owner_decision", "authority_ref": f"telegram:{chat_id}:{message_id}", "revision": 1},
                "issued_at": utc_now(), "payload": {
                    "intent": "approve", "decision_type": "correction", "grant_id": f"correction:{review['review_id']}",
                    "proposal_ref": f"findings:{review['review_id']}", "proposal_revision": 1,
                    "repository_id": repository_id, "repository_visibility": "private", "linked_issue": True,
                    "base_revision": candidate["candidate_revision"], "scope": scope, "executor": "claude_code",
                    "executor_registration_ref": next(iter(self.config.executor_registrations)),
                    "acceptance_conditions": ["accepted findings resolved", "fresh verification passes"],
                    "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
                    "prohibited_actions": ["merge", "release", "deploy"], "accepted_finding_refs": finding_ids,
                    "source_trace_id": source_trace_id,
                },
            }
            self.coordinator.submit(command)
            return "collaboration-correction-grant-recorded"
        if review.get("verdict") == "pass":
            if review.get("checks_passed") is not True or review.get("repository_current") is not True:
                return "collaboration-owner-clarification-required"
            repository_id = str(next(event["payload"]["repository_id"] for event in reversed(self.coordinator.store.events(case_id)) if event["event_type"] == "issue_linked"))
            self.coordinator.record_merge_decision({
                "decision_id": f"telegram:{chat_id}:{message_id}:merge", "case_id": case_id,
                "owner_id": self.config.owner_ids[0], "repository": repository_id,
                "candidate_revision": candidate["candidate_revision"], "review_id": review["review_id"],
                "checks_passed": review.get("checks_passed") is True,
                "repository_current": review.get("repository_current") is True,
                "current": review.get("checks_passed") is True and review.get("repository_current") is True,
                "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(),
            }, actor)
            return "collaboration-merge-decision-recorded"
        return "collaboration-owner-clarification-required"

    def _owner_targets(self) -> dict[str, dict[str, Any]]:
        targets: dict[str, dict[str, Any]] = {}
        for row in self.coordinator.store.list_cases(None):
            case_id = str(row["case_id"])
            events = self.coordinator.store.events(case_id)
            issue = next((event for event in reversed(events) if event["event_type"] == "issue_linked"), None)
            if issue is None:
                continue
            analysis = next((event for event in reversed(events) if event["event_type"] == "analysis_terminal"), None)
            repository_id = str(issue["payload"]["repository_id"])
            common = {
                "current": True, "representative_id": self.config.representative_id,
                "repository_id": repository_id, "repository_visibility": "private",
                "linked_issue": issue["payload"].get("issue_ref"), "executor_registration_ref": next(iter(self.config.executor_registrations)),
                "issued_at": utc_now(), "source_trace_id": issue.get("source_trace_id"),
            }
            if analysis is None:
                targets[case_id] = {
                    **common, "decision_type": "issue_analysis", "executor": "codex",
                    "scope": ["."], "acceptance_conditions": ["evidence-backed non-mutating analysis"],
                }
            else:
                proposal = dict(analysis["payload"].get("proposal") or {})
                required_proposal = {"proposal_ref", "proposal_revision", "base_revision", "scope", "acceptance_conditions"}
                if not required_proposal <= proposal.keys() or any(proposal.get(key) in (None, "", []) for key in required_proposal):
                    continue
                targets[case_id] = {
                    **common, "decision_type": "implementation", "executor": "claude_code",
                    "grant_id": f"grant:{case_id}:{len(events)}",
                    "proposal_ref": proposal["proposal_ref"],
                    "proposal_revision": proposal["proposal_revision"],
                    "base_revision": proposal["base_revision"],
                    "scope": proposal["scope"],
                    "acceptance_conditions": proposal["acceptance_conditions"],
                    "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
                    "prohibited_actions": ["merge", "release", "deploy"],
                }
        return targets

    def _recipient_for(self, event: Mapping[str, Any]) -> str:
        payload = event.get("payload", {})
        recipient = payload.get("recipient_representative_id")
        if recipient in self.config.peers:
            return str(recipient)
        origin = event.get("origin", {}).get("representative_id")
        if origin in self.config.peers:
            return str(origin)
        case_id = str(event.get("case_id") or "")
        if case_id:
            for prior in reversed(self.coordinator.store.events(case_id)):
                if prior.get("event_type") != "handoff_recorded":
                    continue
                handoff = prior.get("payload", {})
                sender = handoff.get("sender_representative_id")
                recipient = handoff.get("recipient_representative_id")
                counterpart = recipient if sender == self.config.representative_id else sender
                if counterpart in self.config.peers:
                    return str(counterpart)
        raise PeerDeliveryError("cannot determine exact Peer recipient")

    def _schedule(self, coroutine: Any) -> None:
        try:
            task = asyncio.get_running_loop().create_task(coroutine)
        except RuntimeError:
            coroutine.close()
            return
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    @staticmethod
    def _platform_name(event: Any) -> str:
        platform = getattr(getattr(event, "source", None), "platform", None)
        if platform is None:
            platform = getattr(event, "platform", None)
        return str(getattr(platform, "value", platform) or "").lower()

    @staticmethod
    def _telegram_adapter(gateway: Any) -> Any | None:
        for key, adapter in getattr(gateway, "adapters", {}).items():
            if str(getattr(key, "value", key) or "").lower() == "telegram":
                return adapter
        return None

    @staticmethod
    def _owner_text(summary: Mapping[str, Any], payload: Mapping[str, Any]) -> str:
        entries = summary.get("entries") or []
        text = entries[0].get("content", "") if entries else ""
        return f"Hermes Collaboration {summary.get('case_id')}\nReason: {payload.get('reason', 'case_update')}\n{text}"[:3500]
