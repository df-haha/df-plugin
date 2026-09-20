from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any, Iterable


class GitHubWebhook:
    def __init__(self, secret: bytes, *, allowed_repositories: Iterable[str], allowed_actors: Iterable[str] = (), allowed_intake_agents: Iterable[str] = (), machine_login: str | None = None, allowed_labels: Iterable[str] = ()) -> None:
        self.secret = secret
        self.allowed_repositories = frozenset(allowed_repositories)
        self.allowed_actors = frozenset(allowed_actors)
        if not self.allowed_actors:
            raise ValueError("at least one allowed actor is required")
        self.allowed_intake_agents = frozenset(allowed_intake_agents)
        if not self.allowed_intake_agents <= self.allowed_actors:
            raise ValueError("intake agents must be allowed actors")
        self.machine_login = machine_login
        self.allowed_labels = frozenset(allowed_labels)

    def admit(self, body: bytes, signature: str, delivery_id: str) -> dict[str, Any]:
        expected = "sha256=" + hmac.new(self.secret, body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature): raise PermissionError("invalid GitHub webhook signature")
        data = json.loads(body)
        repo = data.get("repository", {})
        if repo.get("private") is not True or repo.get("full_name") not in self.allowed_repositories: raise PermissionError("repository is not an authorized private repository")
        actor = data.get("sender", {}).get("login")
        if actor not in self.allowed_actors: raise PermissionError("unauthorized GitHub actor")
        issue = data.get("issue", {})
        if not issue.get("node_id") and issue.get("number") is None:
            raise PermissionError("ambiguous Issue identity")
        action = data.get("action")
        if action == "assigned" and data.get("assignee", {}).get("login") == self.machine_login:
            trigger = "assignment"
        elif action == "labeled" and data.get("label", {}).get("name") in self.allowed_labels:
            trigger = "label"
        elif action == "created" and self.machine_login and f"@{self.machine_login}" in data.get("comment", {}).get("body", ""):
            trigger = "mention"
        elif action == "created" and data.get("comment") is not None:
            trigger = "comment"
        elif action == "opened" and actor in self.allowed_intake_agents:
            trigger = "intake_creation"
        else:
            raise PermissionError("Issue event is not an authorized collaboration trigger")
        identity = issue.get("node_id") or f"number:{issue['number']}"
        return {"delivery_id": delivery_id, "duplicate": False, "repository_id": repo["full_name"], "issue_number": issue.get("number"), "issue_identity": identity, "trigger": trigger, "actor": actor, "source_trace_id": f"github:{repo['full_name']}:{identity}"}
