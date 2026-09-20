from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping


class ExternalActionUncertain(RuntimeError):
    pass


class GitHubDelivery:
    """Idempotent draft-PR boundary with optional durable Case storage."""
    def __init__(self, client: Any | None = None, *, store: Any | None = None, case_id: str | None = None) -> None:
        self.client = client
        self.store = store
        self.case_id = case_id
        self._artifacts: dict[tuple[str, str, str], dict[str, Any]] = {}
        self._merges: dict[str, dict[str, Any]] = {}

    def lookup_issue(self, repository: str, number: int) -> Any:
        return self.client.lookup_issue(repository, number) if self.client else None

    @staticmethod
    def _require_draft(result: Mapping[str, Any]) -> dict[str, Any]:
        if result.get("draft") is not True:
            raise PermissionError("only draft PR creation is authorized")
        return dict(result)

    def publish_draft(self, request: Mapping[str, Any]) -> dict[str, Any]:
        required = {"repository", "repository_visibility", "source_trace_id", "candidate_revision", "base_revision", "branch", "commit", "title", "body"}
        if not required <= request.keys() or any(request.get(key) in (None, "") for key in required):
            raise ValueError("incomplete candidate delivery request")
        if request["repository_visibility"] != "private":
            raise PermissionError("only an authorized private repository is supported")
        metadata = f"{request['title']}\n{request['body']}"
        if any(marker in metadata.upper() for marker in ("GH_TOKEN=", "GITHUB_TOKEN=", "BEGIN OPENSSH PRIVATE KEY", "TELEGRAM_BOT_TOKEN=")):
            raise PermissionError("secret-bearing GitHub metadata is forbidden")
        key = (str(request["repository"]), str(request["source_trace_id"]), str(request["candidate_revision"]))
        action_key = "draft_pr:" + ":".join(key[:2])
        if self.store is not None:
            durable = self.store.external_action(action_key)
            if durable and durable.get("status") == "confirmed":
                prior_artifact = self.store.external_artifact(action_key)
                if prior_artifact and prior_artifact.get("candidate_revision") == key[2]:
                    return self._require_draft({field: value for field, value in durable.items() if field != "status"})
                if self.client is None or not self.case_id or prior_artifact is None:
                    raise ExternalActionUncertain("linked draft PR update requires durable identity and GitHub client")
                result = {field: value for field, value in durable.items() if field != "status"}
                update_key = "draft_pr_update:" + ":".join(key)
                update = self.store.external_action(update_key)
                if update and update.get("status") == "confirmed":
                    return self._require_draft(result)
                if update is not None:
                    reconciled = self.client.find_delivery(key[0], key[1], key[2], "push")
                    if reconciled is None:
                        raise ExternalActionUncertain("candidate push outcome requires exact reconciliation")
                    self.store.confirm_external_artifact(
                        update_key, self.case_id,
                        {**result, "artifact_type": "draft_pr", "candidate_revision": key[2], "source_trace_id": key[1]},
                        artifact_key=action_key,
                    )
                    return self._require_draft(result)
                else:
                    self.store.record_external_action(update_key, self.case_id, "draft_pr_update", "pending", {})
                try:
                    self.client.push_candidate(request["repository"], request["branch"], request["commit"])
                except TimeoutError as exc:
                    reconciled = self.client.find_delivery(key[0], key[1], key[2], "push")
                    if reconciled is None:
                        raise ExternalActionUncertain("candidate push outcome is uncertain") from exc
                self.store.confirm_external_artifact(
                    update_key, self.case_id,
                    {**result, "artifact_type": "draft_pr", "candidate_revision": key[2], "source_trace_id": key[1]},
                    artifact_key=action_key,
                )
                return self._require_draft(result)
            if durable and durable.get("status") in {"pending", "uncertain"}:
                if self.client is None:
                    raise ExternalActionUncertain("draft PR outcome requires exact reconciliation")
                reconciled = self.client.find_delivery(key[0], key[1], key[2], "draft_pr")
                if reconciled is None:
                    raise ExternalActionUncertain("draft PR outcome is uncertain")
                reconciled = self._require_draft(reconciled)
                artifact = {**dict(reconciled), "artifact_type": "draft_pr", "candidate_revision": key[2], "source_trace_id": key[1]}
                self.store.confirm_external_artifact(action_key, str(self.case_id), artifact)
                return dict(reconciled)
            if not self.case_id:
                raise ValueError("case_id is required for durable GitHub delivery")
            self.store.record_external_action(action_key, self.case_id, "draft_pr", "pending", {})
        if key in self._artifacts:
            return dict(self._artifacts[key])
        if self.client is None and self.store is not None:
            raise PermissionError("GitHub client is required for durable draft PR creation")
        if self.client is None:
            result = {"number": len(self._artifacts) + 1, "url": f"https://github.invalid/{request['repository']}/pull/{len(self._artifacts) + 1}", "draft": True}
        else:
            result = None
            operations = (
                ("branch", "create_branch", (request["repository"], request["branch"], request["base_revision"])),
                ("push", "push_candidate", (request["repository"], request["branch"], request["commit"])),
                ("draft_pr", "create_draft_pr", (request["repository"], request["branch"], request["source_trace_id"], request["candidate_revision"], request["title"], request["body"])),
            )
            for stage, method_name, args in operations:
                operation = getattr(self.client, method_name)
                try:
                    outcome = operation(*args)
                except TimeoutError as exc:
                    outcome = self.client.find_delivery(request["repository"], request["source_trace_id"], request["candidate_revision"], stage)
                    if outcome is None:
                        raise ExternalActionUncertain(f"{stage} outcome is uncertain") from exc
                if stage == "draft_pr":
                    result = outcome
            if result is None:
                raise ExternalActionUncertain("draft PR outcome is missing")
        result = self._require_draft(result)
        self._artifacts[key] = dict(result)
        if self.store is not None:
            self.store.confirm_external_artifact(
                action_key, self.case_id,
                {**dict(result), "artifact_type": "draft_pr", "candidate_revision": key[2], "source_trace_id": key[1]},
            )
        return dict(result)

    def merge(self, repository: str, candidate_revision: str, decision: Mapping[str, Any] | None) -> dict[str, Any]:
        if not decision or decision.get("repository") != repository or decision.get("candidate_revision") != candidate_revision or not decision.get("current") or decision.get("checks_passed") is not True or decision.get("repository_current") is not True or not decision.get("review_id"):
            raise PermissionError("current candidate-bound MergeDecision required")
        try:
            expired = datetime.fromisoformat(str(decision.get("expires_at")).replace("Z", "+00:00")) <= datetime.now(timezone.utc)
        except (TypeError, ValueError):
            expired = True
        if expired:
            raise PermissionError("MergeDecision is expired")
        decision_id = str(decision.get("decision_id") or "")
        if not decision_id:
            raise PermissionError("durable MergeDecision identity required")
        if decision_id in self._merges:
            return dict(self._merges[decision_id])
        if self.client:
            try:
                result = self.client.merge(repository, candidate_revision, decision_id)
            except TimeoutError as exc:
                result = self.client.find_merge(repository, candidate_revision, decision_id)
                if result is None:
                    raise ExternalActionUncertain("merge outcome is uncertain") from exc
        else:
            result = {"merged": True, "candidate_revision": candidate_revision, "decision_id": decision_id}
        self._merges[decision_id] = dict(result)
        return dict(result)

    def release(self, repository: str, decision: Mapping[str, Any] | None = None) -> None:
        raise PermissionError("MergeDecision does not authorize release")

    def deploy(self, repository: str, decision: Mapping[str, Any] | None = None) -> None:
        raise PermissionError("MergeDecision does not authorize deployment")
