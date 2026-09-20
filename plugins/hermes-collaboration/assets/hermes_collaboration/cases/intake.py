from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping


def assess_duplicates(source_trace_id: str, feedback_hash: str, repository_id: str, candidates: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    bounded = list(candidates)[:20]
    return {"assessment_id": f"assessment:{source_trace_id}", "source_trace_id": source_trace_id, "source_feedback_hash": feedback_hash, "repository_id": repository_id, "candidates": bounded, "recommendation": "create_and_flag_relation" if bounded else "create_new_issue"}


def delegate_to_predecessor(contract_call: Callable[[Mapping[str, Any]], Mapping[str, Any]], artifact: Mapping[str, Any]) -> dict[str, Any]:
    if not artifact.get("source_trace_id"): raise ValueError("source_trace_id required")
    return dict(contract_call(dict(artifact)))


class CaseIntake:
    """Advisory duplicate assessment wrapped around unchanged Issue creation."""

    def __init__(self, *, search: Callable[[str, str, int], Iterable[Mapping[str, Any]]], create_issue: Callable[[Mapping[str, Any]], Mapping[str, Any]]) -> None:
        self.search = search
        self.create_issue = create_issue

    def admit_external_feedback(self, artifact: Mapping[str, Any]) -> dict[str, Any]:
        trace = str(artifact.get("source_trace_id") or "")
        repository = str(artifact.get("repository_id") or "")
        feedback = str(artifact.get("feedback") or "")
        if not trace or not repository or not feedback:
            raise ValueError("source_trace_id, repository_id, and feedback are required")
        candidates = list(self.search(repository, feedback[:1000], 20))[:20]
        assessment = assess_duplicates(
            trace,
            "sha256:" + hashlib.sha256(feedback.encode()).hexdigest(),
            repository,
            candidates,
        )
        assessment["assessed_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        issue = dict(self.create_issue({**dict(artifact), "duplicate_assessment": assessment}))
        return {"assessment": assessment, "issue": issue, "external_actions": ["create_issue"]}


class PredecessorAttachment:
    """Contract-only bridge; it never imports the predecessor runtime."""

    def __init__(self, contract_call: Callable[[Mapping[str, Any]], Mapping[str, Any]], *, store: Any | None = None) -> None:
        self.contract_call = contract_call
        self.store = store
        self._attachments: dict[str, str] = {}

    def delegate(self, artifact: Mapping[str, Any]) -> dict[str, Any]:
        return delegate_to_predecessor(self.contract_call, artifact)

    def attach(self, source_trace_id: str, case_id: str) -> None:
        if self.store is not None:
            self.store.attach_predecessor(source_trace_id, case_id)
            return
        prior = self._attachments.get(source_trace_id)
        if prior and prior != case_id:
            raise ValueError("source_trace_id is already attached to another Case")
        self._attachments[source_trace_id] = case_id

    def predecessor_authority(self, source_trace_id: str) -> str:
        attached = self.store.predecessor_case(source_trace_id) if self.store is not None else self._attachments.get(source_trace_id)
        return "superseded" if attached else "active"

    def approve(self, source_trace_id: str) -> dict[str, str]:
        if self.predecessor_authority(source_trace_id) == "superseded":
            raise PermissionError("predecessor authority is superseded by the Case")
        return {"status": "approved", "source_trace_id": source_trace_id}
