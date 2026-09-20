from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
import json
from pathlib import Path
from typing import Any, Callable, Mapping


class SafetyViolation(RuntimeError): pass


def _tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        digest.update(str(path.relative_to(root)).encode()); digest.update(path.read_bytes())
    return digest.hexdigest()


def _remove_git_remotes(checkout: Path, env: Mapping[str, str]) -> None:
    if not (checkout / ".git").exists():
        return
    completed = subprocess.run(
        ["git", "remote"], cwd=checkout, env=env, check=True, capture_output=True, text=True,
    )
    for remote in completed.stdout.splitlines():
        subprocess.run(
            ["git", "remote", "remove", remote], cwd=checkout, env=env, check=True,
            capture_output=True, text=True,
        )


class ExecutorAdapter:
    def __init__(self, name: str, runner: Callable[[Mapping[str, Any], Mapping[str, str]], Mapping[str, Any]]) -> None:
        self.name, self.runner = name, runner

    def run(self, request: Mapping[str, Any]) -> dict[str, Any]:
        capability = request.get("capability")
        non_mutating_capabilities = {"issue_analysis", "pr_review", "reanalysis"}
        mutating_capabilities = {"implementation", "correction"}
        if capability not in non_mutating_capabilities | mutating_capabilities:
            raise SafetyViolation("unsupported Executor capability")
        serialized = json.dumps(request, sort_keys=True, default=str).upper()
        if any(marker in serialized for marker in ("GITHUB_TOKEN", "GH_TOKEN", "TELEGRAM_BOT_TOKEN", "BEGIN OPENSSH PRIVATE KEY", "API_KEY")):
            raise SafetyViolation("secret-bearing Executor prompt is forbidden")
        if capability in mutating_capabilities:
            required = {"base_revision", "scope", "acceptance_conditions", "prohibited_actions"}
            if not required <= request.keys() or any(request.get(key) in (None, "", []) for key in required):
                raise SafetyViolation("implementation request is not pinned and bounded")
            normalized_scope = [Path(os.path.normpath(str(value))).as_posix() for value in request["scope"]]
            if any(
                path == ".git" or path.startswith(".git/")
                or path == ".github/workflows" or path.startswith(".github/workflows/")
                for path in normalized_scope
            ):
                raise SafetyViolation("repository control-plane scope is forbidden")
        root = Path(str(request["repository_path"]))
        if not root.is_dir():
            raise SafetyViolation("repository checkout is unavailable")
        allowed_env = {"PATH", "LANG", "LC_ALL", "TZ"}
        env = {key: os.environ[key] for key in allowed_env if key in os.environ}
        env.update({"HERMES_EXECUTOR_MODE": str(capability), "GIT_TERMINAL_PROMPT": "0"})
        non_mutating = capability in non_mutating_capabilities
        original_before = _tree_hash(root)
        mutating_temp = None
        if non_mutating:
            with tempfile.TemporaryDirectory(prefix="hermes-collaboration-executor-") as temp:
                checkout = Path(temp) / "repository"
                shutil.copytree(root, checkout, symlinks=False)
                _remove_git_remotes(checkout, env)
                before = _tree_hash(checkout)
                bounded_request = {**dict(request), "repository_path": str(checkout), "allowed_actions": ["read", "test"]}
                result = dict(self.runner(bounded_request, env))
                after = _tree_hash(checkout)
                original_after = _tree_hash(root)
                if before != after or original_before != original_after:
                    raise SafetyViolation("non-mutating Executor changed repository")
        else:
            mutating_temp = tempfile.TemporaryDirectory(prefix="hermes-collaboration-executor-")
            checkout = Path(mutating_temp.name) / "repository"
            shutil.copytree(root, checkout, symlinks=False)
            _remove_git_remotes(checkout, env)
            before = _tree_hash(checkout)
            bounded_request = {
                **dict(request), "repository_path": str(checkout),
                "allowed_actions": ["read", "test", "edit"],
            }
            try:
                result = dict(self.runner(bounded_request, env))
            except BaseException:
                mutating_temp.cleanup()
                raise
            after = _tree_hash(checkout)
        if capability in mutating_capabilities:
            prohibited = set(request["prohibited_actions"])
            for mutation in result.get("mutations", []):
                if mutation.get("kind") in prohibited or mutation.get("kind") in {"merge", "release", "deploy"}:
                    raise SafetyViolation("Executor reported a prohibited external mutation")
            def normalized_relative(value: Any) -> str | None:
                raw = Path(str(value))
                if raw.is_absolute():
                    return None
                normalized = Path(os.path.normpath(raw))
                if normalized == Path("..") or ".." in normalized.parts:
                    return None
                return normalized.as_posix()

            raw_changed = [item.get("target") for item in result.get("mutations", []) if item.get("kind") == "file" and item.get("outcome") == "confirmed"]
            changed_scope = [normalized_relative(path) for path in raw_changed]
            approved_scope = [path for path in (normalized_relative(value) for value in request["scope"]) if path is not None]
            outside_scope = [path for path in changed_scope if path is not None and not any(allowed == "." or path == allowed or path.startswith(allowed.rstrip("/") + "/") for allowed in approved_scope)]
            outside_scope.extend(str(path) for path, normalized in zip(raw_changed, changed_scope) if normalized is None)
            result["changed_scope"] = [path for path in changed_scope if path is not None]
            if outside_scope or result.get("material_deviations"):
                result["status"] = "blocked"
                result["scope_violation"] = outside_scope
            if any(not isinstance(item.get("command"), list) or item.get("exit_code") != 0 for item in result.get("verification", [])) or not result.get("verification"):
                result["status"] = "failed"
            if result.get("status") == "review_ready":
                for relative in result["changed_scope"]:
                    source = checkout / relative
                    destination = root / relative
                    if source.is_symlink() or not source.is_file():
                        raise SafetyViolation("approved mutation is not a regular file")
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, destination)
                after = _tree_hash(root)
        original_after = _tree_hash(root)
        result.update(executor=self.name, repository_drift=original_before != original_after, pre_hash=original_before, post_hash=original_after)
        try:
            return result
        finally:
            if mutating_temp is not None:
                mutating_temp.cleanup()
