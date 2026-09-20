from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Mapping, Sequence

from .contracts import content_hash, utc_now
from .errors import AuthorizationError, StateConflictError, ValidationError
from ..storage.sqlite_case_store import SQLiteCaseStore


@dataclass(frozen=True)
class Decision:
    status: str
    case_id: str
    event_ids: tuple[str, ...] = ()
    work_id: str | None = None


@dataclass(frozen=True)
class Receipt:
    status: str
    case_id: str


@dataclass(frozen=True)
class WorkItem:
    work_id: str
    case_id: str
    capability: str
    executor: str
    payload: Mapping[str, Any]


class CaseCoordinator:
    def __init__(self, store: SQLiteCaseStore, representative_id: str, *, peer_trust: Mapping[str, int] | None = None, executor_registrations: Mapping[str, int] | None = None, owner_ids: Sequence[str] = ("owner-a",), intake_agents: Sequence[str] = ("intake-a",), authorized_repositories: Sequence[str] = ("owner/private",)) -> None:
        self.store = store
        self.representative_id = representative_id
        self.peer_trust = dict(peer_trust or {})
        self.executor_registrations = dict(executor_registrations or {})
        self.owner_ids = frozenset(owner_ids)
        self.intake_agents = frozenset(intake_agents)
        self.authorized_repositories = frozenset(authorized_repositories)
        self._submit_lock = RLock()

    def _authorize(self, command: Mapping[str, Any], trusted_ingress: str | None = None) -> None:
        actor = command.get("actor", {})
        authority = command.get("authority", {})
        category = actor.get("category")
        if category == "executor":
            ref = str(actor.get("actor_ref"))
            registration_revision = actor.get("registration_revision", authority.get("revision"))
            if ref not in self.executor_registrations or registration_revision is None or self.executor_registrations[ref] != registration_revision:
                raise AuthorizationError("inactive or mismatched ExecutorRegistration")
        elif category == "owner":
            if actor.get("actor_ref") not in self.owner_ids:
                raise AuthorizationError("unauthorized Owner")
        elif category == "peer":
            ref = str(actor.get("representative_id"))
            revision = authority.get("revision")
            if ref not in self.peer_trust or revision is None or self.peer_trust[ref] != revision:
                raise AuthorizationError("inactive or mismatched PeerTrust")
        elif category == "intake_agent" and actor.get("actor_ref") not in self.intake_agents:
            raise AuthorizationError("unauthorized Intake Agent")
        elif category == "representative" and (actor.get("actor_ref") != self.representative_id or actor.get("representative_id") != self.representative_id):
            raise AuthorizationError("unauthorized Representative")
        elif category == "github" and trusted_ingress != "github_webhook":
            raise AuthorizationError("GitHub commands require verified webhook ingress")
        elif category == "system" and trusted_ingress != "internal_system":
            raise AuthorizationError("system commands require trusted in-process ingress")
        elif category not in {"github", "intake_agent", "representative", "system"}:
            raise AuthorizationError("unknown actor category")

    def _event(self, case_id: str, event_type: str, command: Mapping[str, Any], payload: Mapping[str, Any], suffix: str, *, sequence: int | None = None) -> dict[str, Any]:
        event = {"schema_version": 1, "event_id": f"{command['command_id']}:{suffix}", "case_id": case_id, "event_type": event_type, "origin": {"representative_id": self.representative_id, "sequence": sequence if sequence is not None else self.store.next_sequence(self.representative_id)}, "actor_category": command["actor"]["category"], "authority_ref": command["authority"]["authority_ref"], "occurred_at": command.get("issued_at") or utc_now(), "source_trace_id": command.get("source_trace_id"), "predecessor_event_ids": [], "payload": dict(payload)}
        event["content_hash"] = content_hash(event)
        return event

    def submit(self, command: Mapping[str, Any]) -> Decision:
        return self._submit_with_ingress(command, trusted_ingress=None)

    def submit_ingress(self, command: Mapping[str, Any], *, ingress: str) -> Decision:
        if ingress not in {"github_webhook", "internal_system"}:
            raise AuthorizationError("unknown trusted ingress")
        return self._submit_with_ingress(command, trusted_ingress=ingress)

    def _submit_with_ingress(self, command: Mapping[str, Any], *, trusted_ingress: str | None) -> Decision:
        try:
            with self._submit_lock:
                return self._submit(command, trusted_ingress=trusted_ingress)
        except Exception as exc:
            actor = command.get("actor", {}) if isinstance(command, Mapping) else {}
            authority = command.get("authority", {}) if isinstance(command, Mapping) else {}
            self.store.record_audit(
                event_type=str(command.get("command_type", "invalid_command")) if isinstance(command, Mapping) else "invalid_command",
                actor_category=str(actor.get("category", "unknown")),
                authority_ref=str(authority.get("authority_ref", "missing")),
                content_hash=content_hash(command if isinstance(command, Mapping) else {"invalid": True}),
                occurred_at=str(command.get("issued_at") or utc_now()) if isinstance(command, Mapping) else utc_now(),
                outcome=f"denied:{type(exc).__name__}",
            )
            raise

    def _submit(self, command: Mapping[str, Any], *, trusted_ingress: str | None = None) -> Decision:
        self._authorize(command, trusted_ingress)
        kind = str(command.get("command_type"))
        payload = dict(command.get("payload") or {})
        if kind == "admit_handoff":
            case_id = str(command.get("case_id") or f"case:{payload.get('handoff_id')}")
        else:
            case_id = str(command.get("case_id") or "")
            if not case_id:
                raise ValidationError("case_id is required")
        if kind == "admit_handoff":
            recipient = payload.get("recipient_representative_id")
            if self.peer_trust.get(str(recipient)) is None:
                raise AuthorizationError("recipient Peer is not trusted")
        if kind == "record_owner_decision" and payload.get("intent") in {"approve", "narrow"} and not payload.get("linked_issue"):
            raise AuthorizationError("repository work requires an authorized linked Issue")
        if kind == "record_owner_decision" and command["actor"].get("category") != "owner":
            raise AuthorizationError("Owner Decision requires Owner authority")
        decision_capability = payload.get("capability") or payload.get("decision_type")
        mutating_decision = decision_capability in {"implementation", "correction"}
        if kind == "record_owner_decision" and payload.get("intent") in {"approve", "narrow"} and decision_capability not in {"issue_analysis", "reanalysis", "pr_review", "implementation", "correction"}:
            raise ValidationError("Owner Decision has an unsupported capability")
        if kind == "record_owner_decision" and mutating_decision and payload.get("intent") in {"approve", "narrow"}:
            if command["authority"].get("authority_type") != "owner_decision":
                raise AuthorizationError("Execution Grant requires an OwnerDecision authority")
            required = {"grant_id", "proposal_ref", "proposal_revision", "repository_id", "repository_visibility", "base_revision", "scope", "executor", "executor_registration_ref", "acceptance_conditions", "expires_at", "prohibited_actions"}
            if not required <= payload.keys() or any(payload.get(key) in (None, "", []) for key in required):
                raise ValidationError("Execution Grant is missing bounded authority fields")
            if payload["repository_visibility"] != "private" or payload["repository_id"] not in self.authorized_repositories:
                raise AuthorizationError("Execution Grant repository is unauthorized")
            if payload["executor"] not in {"codex", "claude_code"} or payload["executor_registration_ref"] not in self.executor_registrations:
                raise AuthorizationError("Execution Grant Executor is unavailable")
            if not {"merge", "release", "deploy"} <= set(payload["prohibited_actions"]):
                raise ValidationError("Execution Grant must prohibit merge, release, and deploy")
            if decision_capability == "correction":
                accepted = self.store.accepted_findings(case_id)
                requested = set(payload.get("accepted_finding_refs") or [])
                if not requested or not requested <= set(accepted):
                    raise AuthorizationError("Correction Grant requires only accepted finding IDs")
                allowed_scope = {path for finding_id in requested for path in accepted[finding_id].get("affected_scope", [])}
                if not set(payload["scope"]) <= allowed_scope:
                    raise AuthorizationError("Correction Grant scope exceeds accepted findings")
        if kind == "record_owner_decision" and payload.get("intent") in {"approve", "narrow"}:
            if not any(
                event["event_type"] == "issue_linked"
                and event["payload"].get("repository_id") == payload.get("repository_id")
                for event in self.store.events(case_id)
            ):
                raise AuthorizationError("repository work requires an authorized linked Issue")
        if kind == "record_policy":
            required = {"policy_id", "revision", "status", "intake_agent_id", "repository_id", "repository_visibility", "executor_registration_ref", "executor", "capability"}
            if not required <= payload.keys() or payload["status"] != "active" or payload["repository_visibility"] != "private" or payload["capability"] != "initial_issue_analysis_non_mutating":
                raise ValidationError("invalid StandingAnalysisPolicy")
            if payload["intake_agent_id"] not in self.intake_agents or payload["repository_id"] not in self.authorized_repositories or payload["executor_registration_ref"] not in self.executor_registrations:
                raise AuthorizationError("StandingAnalysisPolicy binding is unauthorized")
        if kind == "admit_issue":
            if payload.get("repository_visibility") != "private" or payload.get("repository_id") not in self.authorized_repositories:
                raise AuthorizationError("repository is not an authorized private repository")
        if kind == "close_case":
            reasons = {"explicit_owner_close", "merged_and_confirmed", "rejected", "expired", "cancelled"}
            if payload.get("reason") not in reasons:
                raise ValidationError("invalid Case closure reason")
            if payload["reason"] == "merged_and_confirmed" and not self.store.current_merge_decision(case_id):
                raise AuthorizationError("merged-and-confirmed closure requires a current MergeDecision")
            if command["actor"].get("category") != "owner" and not (
                command["actor"].get("category") == "system"
                and payload["reason"] in {"expired", "merged_and_confirmed"}
            ):
                raise AuthorizationError("Case closure requires Owner authority")
        if kind == "record_owner_decision" and payload.get("intent") in {"pause", "stop"} and command["actor"].get("category") != "owner":
            raise AuthorizationError("pause or stop requires Owner authority")
        discussion = None
        decision_status = "accepted"
        event_override = None
        if kind == "start_discussion":
            if command["authority"].get("authority_type") != "discussion_mandate" or command["authority"].get("authority_ref") != payload.get("mandate_id"):
                raise AuthorizationError("DiscussionMandate authority is required")
            required = {"mandate_id", "objective", "participants", "permitted_actions", "prohibited_commitments", "turn_limit", "time_limit_seconds", "cost_limit", "expires_at", "stop_conditions"}
            if not required <= payload.keys() or any(payload.get(key) in (None, "", []) for key in required):
                raise ValidationError("DiscussionMandate is incomplete")
            if len(set(payload["participants"])) < 2 or payload["turn_limit"] < 1 or payload["time_limit_seconds"] < 1 or payload["cost_limit"] <= 0:
                raise ValidationError("DiscussionMandate limits or participants are invalid")
            expiry = datetime.fromisoformat(str(payload["expires_at"]).replace("Z", "+00:00"))
            if expiry <= datetime.now(timezone.utc):
                raise ValidationError("DiscussionMandate is expired")
            discussion = {**payload, "case_id": case_id, "status": "active", "turn_count": 0, "cost_used": 0.0, "stop_reason": None, "proposal": None}
        elif kind == "record_discussion_turn":
            discussion = self.store.discussion(str(payload.get("mandate_id")))
            if not discussion or discussion["case_id"] != case_id or discussion["status"] != "active" or command["authority"].get("authority_ref") != discussion["mandate_id"]:
                raise StateConflictError("DiscussionMandate is not current and active")
            if payload.get("speaker") not in discussion["participants"] or command["actor"].get("actor_ref") != payload.get("speaker"):
                raise AuthorizationError("Discussion speaker is not an authorized participant")
            next_turn = discussion["turn_count"] + 1
            next_cost = float(discussion["cost_used"]) + float(payload.get("cost", 0))
            stop_reason = None
            if next_turn > discussion["turn_limit"]:
                stop_reason = "turn_limit"
            elif next_cost > discussion["cost_limit"]:
                stop_reason = "cost_limit"
            elif float(payload.get("elapsed_seconds", 0)) > discussion["time_limit_seconds"]:
                stop_reason = "time_limit"
            elif set(payload.get("proposed_commitments", [])) & set(discussion["prohibited_commitments"]):
                stop_reason = "authority_boundary"
                decision_status = "paused"
                event_override = "discussion_paused"
            elif payload.get("converged") is True:
                stop_reason = "converged"
            if stop_reason and decision_status != "paused":
                decision_status = "stopped"
                event_override = "discussion_stopped"
            status = "paused" if decision_status == "paused" else ("stopped" if decision_status == "stopped" else "active")
            proposal = payload.get("proposal")
            if proposal:
                proposal = {**proposal, "authoritative": False}
            discussion = {**discussion, **payload, "status": status, "turn_count": min(next_turn, discussion["turn_limit"]), "cost_used": min(next_cost, discussion["cost_limit"]), "stop_reason": stop_reason, "proposal": proposal or discussion.get("proposal")}
            payload = discussion
        elif kind == "stop_discussion":
            discussion = self.store.discussion(str(payload.get("mandate_id")))
            if not discussion or discussion["case_id"] != case_id or discussion["status"] not in {"active", "paused"}:
                raise StateConflictError("DiscussionMandate is not stoppable")
            discussion = {**discussion, "status": "stopped", "stop_reason": str(payload.get("reason") or "cancelled")}
            payload = discussion
            decision_status = "stopped"
        digest = content_hash(command)
        prior = self.store.command_status(str(command["command_id"]), digest)
        if prior:
            return Decision("duplicate", prior)
        mapping = {"admit_handoff": "handoff_recorded", "record_owner_decision": "owner_decision_recorded", "record_policy": "policy_recorded", "revoke_policy": "policy_revoked", "start_discussion": "discussion_started", "record_discussion_turn": "discussion_turn_recorded", "stop_discussion": "discussion_stopped", "submit_executor_result": "artifact_recorded" if payload.get("candidate_revision") else "analysis_terminal", "close_case": "case_closed", "admit_issue": "issue_linked", "attach_predecessor": "predecessor_attached", "admit_external_feedback": "duplicate_assessment_recorded"}
        if kind not in mapping:
            raise ValidationError(f"unsupported command_type: {kind}")
        events = []
        precursor_events = []
        if kind == "admit_handoff":
            first_sequence = self.store.next_sequence(self.representative_id)
            admitted = self._event(case_id, "case_admitted", command, {"source": "handoff"}, "admitted", sequence=first_sequence)
            precursor_events.append(admitted)
            events.append(admitted["event_id"])
            event = self._event(case_id, event_override or mapping[kind], command, payload, kind, sequence=first_sequence + 1)
        else:
            event = self._event(case_id, event_override or mapping[kind], command, payload, kind)
        state = {"record_owner_decision": "awaiting_decision", "close_case": "closed", "admit_issue": "awaiting_owner"}.get(kind, "received")
        outbox = []
        work = []
        policy = None
        grant = None
        candidate = None
        cancel_authority_ref = None
        work_update = None
        case_work_status = None
        work_id = None
        if kind == "admit_handoff":
            outbox = [{"kind": "peer", "payload": event}, {"kind": "owner_notification", "payload": {"case_id": case_id, "reason": "handoff_received"}}]
        elif kind == "record_policy":
            policy = payload
        elif kind == "revoke_policy":
            current = self.store.policy(str(payload.get("policy_id")))
            if not current or current["revision"] != payload.get("revision") or current["status"] != "active":
                raise StateConflictError("StandingAnalysisPolicy is not current and active")
            policy = {**current, "status": "revoked"}
            cancel_authority_ref = current["policy_id"]
            outbox = [{"kind": "owner_notification", "payload": {"case_id": case_id, "reason": "policy_revoked"}}]
        elif kind == "admit_issue" and payload.get("trigger") == "intake_creation":
            current = self.store.policy(str(command["authority"]["authority_ref"]))
            if not current or current["status"] != "active" or current["revision"] != command["authority"]["revision"]:
                raise AuthorizationError("no current StandingAnalysisPolicy")
            if current["intake_agent_id"] != command["actor"]["actor_ref"] or current["repository_id"] != payload["repository_id"] or current["executor"] != payload["executor"]:
                raise AuthorizationError("StandingAnalysisPolicy does not exactly match")
            state = "queued"
            work = [{
                "work_id": f"analysis-initial:{case_id}", "case_id": case_id,
                "capability": "issue_analysis", "executor": payload["executor"],
                "authority_ref": current["policy_id"], "candidate_revision": None,
                "payload": {**payload, "source_trace_id": command.get("source_trace_id"), "non_mutating": True},
            }]
            outbox = [{"kind": "owner_notification", "payload": {"case_id": case_id, "reason": "analysis_started"}}]
        elif kind == "record_owner_decision" and payload.get("intent") in {"approve", "narrow"}:
            capability = str(decision_capability)
            if capability in {"implementation", "correction"}:
                grant_id = str(payload["grant_id"])
                work_id = f"job:{grant_id}"
                grant = {**payload, "owner_id": command["actor"]["actor_ref"], "status": "active"}
                authority_ref = grant_id
            else:
                work_id = f"work:{command['command_id']}"
                authority_ref = command["authority"]["authority_ref"]
            work = [{
                "work_id": work_id, "case_id": case_id, "capability": capability,
                "executor": payload.get("executor", "codex"),
                "authority_ref": authority_ref,
                "candidate_revision": payload.get("candidate_revision"),
                "payload": {**payload, "owner_id": command["actor"]["actor_ref"]},
            }]
            state = "queued"
            outbox = [{"kind": "owner_notification", "payload": {"case_id": case_id, "reason": f"{capability}_queued"}}]
        elif kind == "submit_executor_result":
            result_status = str(payload.get("status"))
            if result_status not in {"blocked", "uncertain", "failed", "review_ready", "completed", "cancelled"}:
                raise ValidationError("invalid Executor result status")
            state = {
                "blocked": "blocked", "uncertain": "uncertain", "failed": "blocked",
                "review_ready": "review_ready", "completed": "awaiting_decision", "cancelled": "cancelled",
            }[result_status]
            reason = f"analysis_{result_status}" if payload.get("job_id", "").startswith("analysis") else f"execution_{result_status}"
            outbox = [{"kind": "owner_notification", "payload": {"case_id": case_id, "reason": reason}}]
            work_update = (str(payload.get("job_id")), result_status, str(command["authority"]["authority_ref"]))
            if result_status == "review_ready":
                required = {"job_id", "base_revision", "candidate_revision", "changed_scope", "verification"}
                if not required <= payload.keys() or not payload.get("candidate_revision") or not payload.get("verification") or any(item.get("exit_code") != 0 or not isinstance(item.get("command"), list) for item in payload["verification"]):
                    raise ValidationError("review-ready result requires a pinned candidate and fresh argv verification")
                candidate = payload
        elif kind == "record_owner_decision" and payload.get("intent") in {"pause", "stop"}:
            case_work_status = "blocked" if payload["intent"] == "pause" else "cancelled"
            state = case_work_status
            outbox = [{"kind": "owner_notification", "payload": {"case_id": case_id, "reason": f"case_{payload['intent']}", "event_id": event["event_id"]}}]
        elif kind == "close_case":
            state = {"explicit_owner_close": "closed", "merged_and_confirmed": "closed", "rejected": "rejected", "expired": "expired", "cancelled": "cancelled"}[payload["reason"]]
            case_work_status = "cancelled"
            peer_outbox = [{"kind": "peer", "payload": event}] if self._case_peer(case_id) else []
            outbox = peer_outbox + [{"kind": "owner_notification", "payload": {"case_id": case_id, "reason": "case_closed", "event_id": event["event_id"]}}]
        elif kind in {"start_discussion", "record_discussion_turn", "stop_discussion"}:
            state = "blocked" if discussion and discussion["status"] == "paused" else ("awaiting_decision" if discussion and discussion["status"] == "stopped" else "received")
            outbox = [{"kind": "peer", "payload": event}] if self._case_peer(case_id) else []
            if discussion and discussion.get("stop_reason") or payload.get("attention_reason"):
                reason = discussion.get("stop_reason") or payload.get("attention_reason")
                outbox.append({"kind": "owner_notification", "payload": {"case_id": case_id, "reason": reason, "event_id": event["event_id"]}})
        self.store.append(
            case_id=case_id, state=state, event=event, outbox=outbox,
            precursor_events=precursor_events,
            terminal=kind == "close_case", command=(str(command["command_id"]), digest),
            work=work, policy=policy, grant=grant, candidate=candidate, discussion=discussion,
            predecessor_attachment=str(command.get("source_trace_id")) if kind == "attach_predecessor" and command.get("source_trace_id") else None,
            cancel_authority_ref=cancel_authority_ref, work_update=work_update,
            case_work_status=case_work_status,
        )
        events.append(event["event_id"])
        return Decision(decision_status, case_id, tuple(events), work_id)

    def policy_status(self, policy_id: str) -> str | None:
        policy = self.store.policy(policy_id)
        return str(policy["status"]) if policy else None

    def grant_status(self, grant_id: str) -> str | None:
        return self.store.grant_status(grant_id)

    def current_candidate(self, case_id: str) -> dict[str, Any] | None:
        return self.store.current_candidate(case_id)

    def record_candidate(self, candidate: Mapping[str, Any]) -> Decision:
        required = {"case_id", "job_id", "base_revision", "candidate_revision", "producing_session_id", "changed_scope", "verification", "occurred_at"}
        if not required <= candidate.keys() or not candidate.get("verification") or any(item.get("exit_code") != 0 or not isinstance(item.get("command"), list) for item in candidate.get("verification", [])):
            raise ValidationError("CandidateRevision requires fresh argv verification")
        case_id = str(candidate["case_id"])
        command = {"command_id": f"candidate:{candidate['candidate_revision']}", "actor": {"category": "system"}, "authority": {"authority_ref": str(candidate["job_id"])}, "issued_at": candidate["occurred_at"]}
        digest = content_hash(candidate)
        prior = self.store.command_status(command["command_id"], digest)
        if prior:
            return Decision("duplicate", prior)
        event = self._event(case_id, "artifact_recorded", command, candidate, "candidate")
        self.store.append(case_id=case_id, state="review_ready", event=event, command=(command["command_id"], digest), candidate=candidate)
        return Decision("accepted", case_id, (event["event_id"],), str(candidate["job_id"]))

    def record_review(self, review: Mapping[str, Any]) -> Decision:
        required = {"review_id", "case_id", "candidate_revision", "reviewer_registration_ref", "reviewer_job_id", "reviewer_session_id", "verdict", "inputs", "findings", "completed_at"}
        if not required <= review.keys() or review.get("verdict") not in {"pass", "changes_requested", "fail"}:
            raise ValidationError("incomplete PR review")
        case_id = str(review["case_id"])
        candidate = self.store.current_candidate(case_id)
        if not candidate or candidate["candidate_revision"] != review["candidate_revision"]:
            raise StateConflictError("review candidate is not current")
        if review["reviewer_registration_ref"] not in self.executor_registrations:
            raise AuthorizationError("reviewer registration is inactive")
        if review["reviewer_job_id"] == candidate["job_id"] or review["reviewer_session_id"] == candidate.get("producing_session_id"):
            raise ValidationError("PR review must be independent")
        needed_inputs = {"complete_diff", "repository_standards", "approved_scope", "security_constraints", "fresh_verification"}
        if not needed_inputs <= set(review["inputs"]):
            raise ValidationError("PR review inputs are incomplete")
        finding_fields = {"finding_id", "severity", "summary", "evidence_refs", "affected_scope", "recommendation"}
        if any(not finding_fields <= finding.keys() or finding.get("severity") not in {"critical", "important", "minor"} or not finding.get("evidence_refs") or not finding.get("affected_scope") for finding in review["findings"]):
            raise ValidationError("review finding is incomplete")
        command = {"command_id": str(review["review_id"]), "actor": {"category": "executor"}, "authority": {"authority_ref": str(review["reviewer_registration_ref"])}, "issued_at": review["completed_at"]}
        digest = content_hash(review)
        prior = self.store.command_status(command["command_id"], digest)
        if prior:
            return Decision("duplicate", prior)
        event = self._event(case_id, "review_recorded", command, review, "review")
        state = "awaiting_merge" if review["verdict"] == "pass" and not review["findings"] else "awaiting_correction"
        self.store.append(case_id=case_id, state=state, event=event, command=(command["command_id"], digest), review=review, outbox=[{"kind": "owner_notification", "payload": {"case_id": case_id, "reason": "review_result", "event_id": event["event_id"]}}])
        return Decision("accepted", case_id, (event["event_id"],))

    def review_status(self, review_id: str) -> str | None:
        review = self.store.review(review_id)
        return str(review["status"]) if review else None

    def accept_findings(self, case_id: str, finding_ids: Sequence[str], actor: Mapping[str, Any]) -> Decision:
        if actor.get("category") != "owner" or actor.get("actor_ref") not in self.owner_ids:
            raise AuthorizationError("unauthorized finding decision")
        current = self.store.current_review(case_id)
        available = {finding["finding_id"] for finding in (current or {}).get("findings", [])}
        if not finding_ids or not set(finding_ids) <= available:
            raise StateConflictError("findings are not current")
        payload = {"finding_ids": list(finding_ids), "review_id": current["review_id"]}
        command = {"command_id": f"accept:{current['review_id']}:{content_hash(payload)[7:19]}", "actor": actor, "authority": {"authority_ref": f"owner:{actor['actor_ref']}"}, "issued_at": utc_now()}
        digest = content_hash(payload)
        prior = self.store.command_status(command["command_id"], digest)
        if prior:
            return Decision("duplicate", prior)
        event = self._event(case_id, "owner_decision_recorded", command, payload, "findings")
        self.store.append(case_id=case_id, state="awaiting_correction", event=event, command=(command["command_id"], digest), accepted_finding_ids=finding_ids)
        return Decision("accepted", case_id, (event["event_id"],))

    def merge_readiness(self, case_id: str) -> dict[str, Any]:
        candidate = self.store.current_candidate(case_id)
        review = self.store.current_review(case_id)
        if not candidate or not review or review.get("candidate_revision") != candidate.get("candidate_revision") or review.get("verdict") != "pass":
            return {"ready": False, "reason": "fresh_review_required"}
        decision = self.store.current_merge_decision(case_id)
        if not decision:
            return {"ready": False, "reason": "merge_decision_required"}
        if self._expired(decision.get("expires_at")):
            return {"ready": False, "reason": "merge_decision_expired"}
        return {"ready": True, "reason": None, "candidate_revision": candidate["candidate_revision"], "decision_id": decision["decision_id"]}

    def record_merge_decision(self, decision: Mapping[str, Any], actor: Mapping[str, Any]) -> Decision:
        if actor.get("category") != "owner" or actor.get("actor_ref") not in self.owner_ids or actor.get("actor_ref") != decision.get("owner_id"):
            raise AuthorizationError("unauthorized MergeDecision Owner")
        if self._expired(decision.get("expires_at")):
            raise StateConflictError("MergeDecision is expired")
        candidate = self.store.current_candidate(str(decision.get("case_id")))
        review = self.store.current_review(str(decision.get("case_id")))
        if not candidate or not review or decision.get("candidate_revision") != candidate.get("candidate_revision") or decision.get("review_id") != review.get("review_id") or review.get("verdict") != "pass" or decision.get("checks_passed") is not True or decision.get("repository_current") is not True or decision.get("repository") not in self.authorized_repositories or decision.get("current") is not True:
            raise StateConflictError("MergeDecision preconditions are not current")
        case_id = str(decision["case_id"])
        command = {"command_id": str(decision["decision_id"]), "actor": actor, "authority": {"authority_ref": str(decision["decision_id"])}, "issued_at": utc_now()}
        digest = content_hash(decision)
        prior = self.store.command_status(command["command_id"], digest)
        if prior:
            return Decision("duplicate", prior)
        event = self._event(case_id, "merge_recorded", command, decision, "decision")
        self.store.append(case_id=case_id, state="awaiting_merge", event=event, command=(command["command_id"], digest), merge_decision=decision)
        return Decision("accepted", case_id, (event["event_id"],))

    @staticmethod
    def _expired(value: Any) -> bool:
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00")) <= datetime.now(timezone.utc)
        except (TypeError, ValueError):
            return True

    def discussion_status(self, mandate_id: str) -> dict[str, Any] | None:
        return self.store.discussion(mandate_id)

    def revoke_peer(self, peer_id: str) -> None:
        self.peer_trust.pop(peer_id, None)

    def revoke_owner(self, owner_id: str) -> None:
        self.owner_ids = frozenset(item for item in self.owner_ids if item != owner_id)
        self.store.revoke_work_authority("owner_id", owner_id, "owner_authority_revoked")

    def revoke_repository(self, repository_id: str) -> None:
        self.authorized_repositories = frozenset(item for item in self.authorized_repositories if item != repository_id)
        self.store.revoke_work_authority("repository_id", repository_id, "repository_authority_revoked")

    def revoke_grant(self, grant_id: str) -> None:
        if not self.store.revoke_grant(grant_id):
            raise StateConflictError("Execution Grant is not revocable")

    def record_question(self, question: Mapping[str, Any]) -> Decision:
        required = {"question_id", "case_id", "job_id", "grant_id", "asked_by", "responsible_owner", "question", "blocker", "next_action", "occurred_at"}
        if not required <= question.keys() or any(question.get(key) in (None, "") for key in required):
            raise ValidationError("incomplete correlated question")
        if question["asked_by"] not in self.executor_registrations:
            raise AuthorizationError("question sender is not an active Executor")
        digest = content_hash(question)
        prior = self.store.command_status(str(question["question_id"]), digest)
        if prior:
            return Decision("duplicate", prior)
        items = self.store.work_items(str(question["case_id"]))
        item = next((row for row in items if row["work_id"] == question["job_id"]), None)
        if not item or item["status"] != "running" or item["authority_ref"] != question["grant_id"]:
            raise StateConflictError("question does not match the active job and grant")
        command = {
            "command_id": str(question["question_id"]),
            "actor": {"category": "executor", "actor_ref": question["asked_by"]},
            "authority": {"authority_ref": question["grant_id"]},
            "issued_at": question["occurred_at"],
            "source_trace_id": item["payload"].get("source_trace_id"),
        }
        event = self._event(str(question["case_id"]), "question_recorded", command, question, "question")
        current = self.store.case(str(question["case_id"]))
        current.update(blocker=question["blocker"], next_action=question["next_action"], responsible_party=question["responsible_owner"])
        peer_outbox = [{"kind": "peer", "payload": event}] if self._case_peer(str(question["case_id"])) else []
        self.store.append(
            case_id=str(question["case_id"]), state="blocked", event=event, view=current,
            outbox=peer_outbox + [{"kind": "owner_notification", "payload": {"case_id": question["case_id"], "reason": "material_question", "event_id": event["event_id"]}}],
            command=(str(question["question_id"]), digest), question=question,
            work_update=(str(question["job_id"]), "blocked", str(question["grant_id"])),
        )
        return Decision("accepted", str(question["case_id"]), (event["event_id"],), str(question["job_id"]))

    def answer_question(self, answer: Mapping[str, Any], actor: Mapping[str, Any]) -> Decision:
        if actor.get("category") != "owner" or actor.get("actor_ref") not in self.owner_ids:
            raise AuthorizationError("unauthorized Owner answer")
        required = {"answer_id", "question_id", "case_id", "answer", "occurred_at"}
        if not required <= answer.keys() or any(answer.get(key) in (None, "") for key in required):
            raise ValidationError("incomplete correlated answer")
        digest = content_hash(answer)
        prior = self.store.command_status(str(answer["answer_id"]), digest)
        if prior:
            return Decision("duplicate", prior)
        question = self.store.question(str(answer["question_id"]))
        if not question or question["case_id"] != answer["case_id"] or question["status"] != "open":
            raise StateConflictError("question is not current and open")
        payload = {**dict(answer), "job_id": question["job_id"], "grant_id": question["grant_id"], "owner_id": actor["actor_ref"]}
        command = {
            "command_id": str(answer["answer_id"]), "actor": actor,
            "authority": {"authority_ref": str(answer["answer_id"])}, "issued_at": answer["occurred_at"],
        }
        event = self._event(str(answer["case_id"]), "answer_recorded", command, payload, "answer")
        current = self.store.case(str(answer["case_id"]))
        current.update(blocker=None, next_action="Executor resumes the existing job", responsible_party=question["payload"]["asked_by"])
        local_job = next((item for item in self.store.work_items(str(answer["case_id"])) if item["work_id"] == question["job_id"]), None)
        peer_outbox = [{"kind": "peer", "payload": event}] if self._case_peer(str(answer["case_id"])) else []
        self.store.append(
            case_id=str(answer["case_id"]), state="queued", event=event, view=current,
            outbox=peer_outbox, command=(str(answer["answer_id"]), digest),
            answer=(str(answer["question_id"]), str(answer["answer_id"])),
            work_update=(question["job_id"], "queued", question["grant_id"]) if local_job else None,
        )
        return Decision("accepted", str(answer["case_id"]), (event["event_id"],), question["job_id"])

    def _case_peer(self, case_id: str) -> str | None:
        for event in reversed(self.store.events(case_id)):
            if event["event_type"] != "handoff_recorded":
                continue
            payload = event["payload"]
            sender = payload.get("sender_representative_id")
            recipient = payload.get("recipient_representative_id")
            peer = recipient if sender == self.representative_id else sender
            return str(peer) if peer in self.peer_trust else None
        return None

    def view(self, case_id: str, actor: Mapping[str, Any], view_kind: str = "summary", cursor: str | None = None) -> dict[str, Any]:
        if actor.get("category") != "owner" or actor.get("actor_ref") not in self.owner_ids:
            raise AuthorizationError("unauthorized Case view")
        if view_kind not in {"summary", "original_peer", "transcript"}:
            raise ValidationError("unknown view_kind")
        view = self.store.case(case_id)
        events = self.store.events(case_id)
        try:
            start = int(cursor or 0)
        except (TypeError, ValueError) as exc:
            raise ValidationError("invalid cursor") from exc
        if start < 0 or start > len(events):
            raise ValidationError("invalid cursor")
        if view_kind == "summary":
            handoff = next((e for e in reversed(events) if e["event_type"] == "handoff_recorded"), None)
            payload = handoff["payload"] if handoff else {}
            text = (
                f"From {payload.get('sender_representative_id', 'unknown')}. "
                f"Requested: {payload.get('requested_action', 'review this Case')}. "
                f"Outcome: {payload.get('expected_outcome', 'Owner decision required')}. "
                f"Decisions: {', '.join(payload.get('approved_decisions', [])) or 'none'}. "
                f"Unknowns: {', '.join(payload.get('open_questions', [])) or 'none'}. "
                "Recommended next step: inspect the original content and decide whether to link an authorized Issue."
            )[:1000]
            page_entries = [{
                "entry_id": f"summary:{case_id}",
                "provenance": "generated_summary",
                "content_kind": "summary",
                "content": text,
                "occurred_at": view["updated_at"],
            }]
            next_cursor = None
        else:
            selected = events if view_kind == "transcript" else [e for e in events if e["event_type"] == "handoff_recorded"]
            page = selected[start:start + 100]
            page_entries = [{
                "entry_id": e["event_id"],
                "provenance": e["actor_category"],
                "content_kind": "verbatim" if e["event_type"] == "handoff_recorded" else ("question" if e["event_type"] == "question_recorded" or (e["event_type"] == "analysis_terminal" and e["payload"].get("questions")) else ("answer" if e["event_type"] == "answer_recorded" else ("discussion_turn" if e["event_type"] in {"discussion_turn_recorded", "discussion_paused", "discussion_stopped"} and e["payload"].get("turn_id") else "evidence"))),
                "content": str(e["payload"])[:4000],
                "occurred_at": e["occurred_at"],
            } for e in page]
            next_cursor = str(start + len(page)) if start + len(page) < len(selected) else None
        handoff_payload = next((e["payload"] for e in reversed(events) if e["event_type"] == "handoff_recorded"), {})
        decisions = [
            e["payload"] for e in events
            if e["event_type"] in {"owner_decision_recorded", "merge_recorded"}
        ]
        artifacts = [e["payload"] for e in events if e["event_type"] == "artifact_recorded"]
        artifacts.extend(self.store.external_artifacts(case_id))
        attention = [
            e["payload"] for e in events
            if e["event_type"] in {"question_recorded", "discussion_paused"}
            or (e["event_type"] == "analysis_terminal" and e["payload"].get("questions"))
        ]
        view.update(
            view_kind=view_kind,
            responsible_party=view.get("responsible_party", "owner"),
            blocker=view.get("blocker"),
            next_action=view.get("next_action", handoff_payload.get("requested_action")),
            decisions=decisions,
            artifacts=artifacts,
            attention=attention,
            entries=page_entries,
            next_cursor=next_cursor,
        )
        return view

    def ingest_peer(self, event: Mapping[str, Any], sender_representative_id: str, *, delivery_id: str | None = None) -> Receipt:
        try:
            return self._ingest_peer(event, sender_representative_id, delivery_id=delivery_id)
        except Exception as exc:
            self.store.record_audit(
                event_type=str(event.get("event_type", "peer_delivery")), actor_category="peer",
                authority_ref=f"peer:{sender_representative_id}", content_hash=content_hash(event),
                occurred_at=str(event.get("occurred_at") or utc_now()), outcome=f"denied:{type(exc).__name__}",
            )
            raise

    def _ingest_peer(self, event: Mapping[str, Any], sender_representative_id: str, *, delivery_id: str | None = None) -> Receipt:
        if sender_representative_id not in self.peer_trust or event.get("origin", {}).get("representative_id") != sender_representative_id:
            raise AuthorizationError("untrusted or forged Peer origin")
        claimed_hash = event.get("content_hash")
        delivery_key = delivery_id or f"peer:{event['event_id']}"
        if isinstance(claimed_hash, str) and self.store.delivery_seen(delivery_key, claimed_hash):
            return Receipt("duplicate", str(event["case_id"]))
        unsigned_event = dict(event)
        unsigned_event.pop("content_hash", None)
        if not isinstance(claimed_hash, str) or content_hash(unsigned_event) != claimed_hash:
            raise ValidationError("Peer event content hash mismatch")
        allowed_event_types = {
            "handoff_recorded", "question_recorded", "answer_recorded", "discussion_started",
            "discussion_turn_recorded", "discussion_paused", "discussion_stopped", "case_closed",
        }
        event_type = str(event.get("event_type"))
        if event_type not in allowed_event_types:
            raise ValidationError("Peer event type is not allowed")
        if event_type == "handoff_recorded":
            payload = event.get("payload", {})
            if payload.get("sender_representative_id") != sender_representative_id or payload.get("recipient_representative_id") != self.representative_id:
                raise AuthorizationError("Peer Handoff sender or recipient is not exact")
            existing = self.store.events(str(event["case_id"]))
            if existing:
                prior_handoff = next((prior for prior in existing if prior["event_type"] == "handoff_recorded"), None)
                if prior_handoff is None:
                    raise AuthorizationError("Peer Handoff cannot capture an existing local Case")
                if prior_handoff.get("event_id") != event.get("event_id") or prior_handoff.get("content_hash") != claimed_hash:
                    raise AuthorizationError("Peer Handoff cannot replace an existing Peer Handoff")
        else:
            binding = next((
                prior for prior in self.store.events(str(event["case_id"]))
                if prior["event_type"] == "handoff_recorded"
                and sender_representative_id in {
                    prior["payload"].get("sender_representative_id"),
                    prior["payload"].get("recipient_representative_id"),
                }
            ), None)
            if binding is None:
                raise AuthorizationError("Peer is not bound to this Case")
        current = None
        question = None
        answer = None
        work_update = None
        reason = "handoff_received"
        if event.get("event_type") == "question_recorded":
            question = event["payload"]
            required = {"question_id", "case_id", "job_id", "grant_id", "asked_by", "responsible_owner", "question", "blocker", "next_action", "occurred_at"}
            if (
                not required <= question.keys()
                or any(question.get(key) in (None, "") for key in required)
                or question.get("case_id") != event.get("case_id")
                or question.get("responsible_owner") not in self.owner_ids
            ):
                raise ValidationError("Peer question requires the exact local responsible Owner and complete binding")
            reason = "material_question"
            try:
                current = self.store.case(str(event["case_id"]))
            except KeyError:
                current = {"case_id": event["case_id"]}
            current.update(blocker=question.get("blocker"), next_action=question.get("next_action"), responsible_party=question.get("responsible_owner"))
        elif event.get("event_type") == "answer_recorded":
            payload = event["payload"]
            stored_question = self.store.question(str(payload["question_id"]))
            if (
                not stored_question
                or stored_question["case_id"] != str(event["case_id"])
                or stored_question["status"] != "open"
                or stored_question["job_id"] != str(payload.get("job_id"))
                or stored_question["grant_id"] != str(payload.get("grant_id"))
            ):
                raise StateConflictError("Peer answer does not match the durable question binding")
            work = next(
                (item for item in self.store.work_items(str(event["case_id"])) if item["work_id"] == stored_question["job_id"]),
                None,
            )
            if not work or work["status"] != "blocked" or work["authority_ref"] != stored_question["grant_id"]:
                raise StateConflictError("Peer answer question binding is not blocked and current")
            answer = (str(stored_question["question_id"]), str(payload["answer_id"]))
            work_update = (str(stored_question["job_id"]), "queued", str(stored_question["grant_id"]))
            reason = "question_answered"
            current = self.store.case(str(event["case_id"]))
            current.update(blocker=None, next_action="Executor resumes the existing job", responsible_party=stored_question["payload"].get("asked_by", "executor"))
        discussion = None
        if event.get("event_type") in {"discussion_started", "discussion_turn_recorded", "discussion_paused", "discussion_stopped"} and event.get("payload", {}).get("mandate_id"):
            incoming = event["payload"]
            prior_discussion = self.store.discussion(str(incoming["mandate_id"]))
            if event.get("event_type") == "discussion_started":
                required = {"mandate_id", "objective", "participants", "permitted_actions", "prohibited_commitments", "turn_limit", "time_limit_seconds", "cost_limit", "expires_at", "stop_conditions"}
                if not required <= incoming.keys() or any(incoming.get(key) in (None, "", []) for key in required):
                    raise ValidationError("Peer DiscussionMandate is incomplete")
                discussion = {**incoming, "case_id": str(event["case_id"]), "status": "active", "turn_count": 0, "cost_used": 0.0, "stop_reason": None, "proposal": None}
            elif prior_discussion is None or prior_discussion["case_id"] != str(event["case_id"]):
                raise StateConflictError("Peer discussion event has no local mandate binding")
            else:
                mutable = {key: incoming[key] for key in ("status", "turn_count", "cost_used", "stop_reason", "proposal") if key in incoming}
                discussion = {**prior_discussion, **mutable}
        terminal = event.get("event_type") == "case_closed"
        if terminal:
            state = {"explicit_owner_close": "closed", "merged_and_confirmed": "closed", "rejected": "rejected", "expired": "expired", "cancelled": "cancelled"}.get(event.get("payload", {}).get("reason"), "closed")
            reason = "case_closed"
        if not terminal:
            state = "blocked" if question else ("queued" if answer else "received")
        status = self.store.append(
            case_id=str(event["case_id"]),
            state=state,
            event=event,
            view=current,
            outbox=[
                {
                    "kind": "receipt",
                    "payload": {
                        "event_id": event["event_id"],
                        "case_id": event["case_id"],
                        "content_hash": event["content_hash"],
                        "recipient_representative_id": sender_representative_id,
                        "occurred_at": event["occurred_at"],
                    },
                },
                {"kind": "owner_notification", "payload": {"case_id": event["case_id"], "reason": reason, "event_id": event["event_id"]}},
            ],
            delivery=(delivery_key, str(event["content_hash"])),
            question=question,
            answer=answer,
            work_update=work_update,
            discussion=discussion,
            terminal=terminal,
            case_work_status="cancelled" if terminal else None,
            audit_actor_category="peer",
            audit_authority_ref=f"peer:{sender_representative_id}",
        )
        return Receipt(status, str(event["case_id"]))

    def claim_work(self, worker: str, capabilities: Sequence[str]) -> WorkItem | None:
        row = self.store.claim_work(worker, capabilities)
        if not row:
            return None
        payload = row["payload"]
        mutating = row["capability"] in {"implementation", "correction"}
        invalid_owner = payload.get("owner_id") is not None and payload.get("owner_id") not in self.owner_ids
        invalid_repository = payload.get("repository_id") is not None and payload.get("repository_id") not in self.authorized_repositories
        executor_ref = payload.get("executor_registration_ref")
        invalid_executor = executor_ref is not None and executor_ref not in self.executor_registrations
        missing_mutating_authority = mutating and (not payload.get("owner_id") or not payload.get("repository_id") or not executor_ref)
        if invalid_owner or invalid_repository or invalid_executor or missing_mutating_authority:
            self.store.cancel_claimed_work(
                str(row["work_id"]), str(row["authority_ref"]), str(row["case_id"]),
                "queued_authority_revoked",
            )
            return None
        return WorkItem(
            row["work_id"], row["case_id"], row["capability"], row["executor"],
            {**payload, "_authority_ref": row["authority_ref"]},
        )

    def complete_work(self, work_id: str, outcome: Mapping[str, Any]) -> Decision:
        status = "completed" if outcome.get("status") == "success" else "failed"
        self.store.complete_work(work_id, status)
        return Decision(status, str(outcome.get("case_id", "")), work_id=work_id)
