"""Profile-aware, non-secret configuration for Hermes Relay Lite."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping


_SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9_-]{0,62}[a-z0-9])?$")
_TELEGRAM_USERNAME_RE = re.compile(r"^@[A-Za-z][A-Za-z0-9_]{4,31}$")
_EXECUTORS = frozenset({"claude", "codex"})


class ExchangeConfigError(ValueError):
    """Raised when relay configuration is absent or unsafe."""


@dataclass(frozen=True, slots=True)
class PeerConfig:
    name: str
    telegram_username: str
    expected_sender_id: int
    enabled: bool = True


@dataclass(frozen=True, slots=True)
class ReceiveConfig:
    max_message_bytes: int = 3_500
    min_peer_interval_seconds: int = 3


@dataclass(frozen=True, slots=True)
class RepositoryConfig:
    alias: str
    path: Path
    executor: str


@dataclass(frozen=True, slots=True)
class ExecutionConfig:
    enabled: bool = False
    timeout_seconds: int = 1_800
    output_limit_chars: int = 20_000
    repositories: Mapping[str, RepositoryConfig] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ExchangeConfig:
    local_peer: str
    owner_chat_id: int
    peers: Mapping[str, PeerConfig] = field(default_factory=dict)
    receive: ReceiveConfig = field(default_factory=ReceiveConfig)
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)

    def peer(self, name: str) -> PeerConfig:
        try:
            peer = self.peers[name]
        except KeyError as exc:
            raise ExchangeConfigError(f"unknown peer: {name}") from exc
        if not peer.enabled:
            raise ExchangeConfigError(f"peer is disabled: {name}")
        return peer

    def repository(self, alias: str) -> RepositoryConfig:
        if not self.execution.enabled:
            raise ExchangeConfigError("execution is disabled")
        try:
            return self.execution.repositories[alias]
        except KeyError as exc:
            raise ExchangeConfigError(f"unknown repository: {alias}") from exc


def default_config_path() -> Path:
    """Return the active profile's relay configuration path."""
    try:
        from hermes_constants import get_hermes_home

        hermes_home = get_hermes_home()
    except ModuleNotFoundError:
        configured_home = os.environ.get("HERMES_HOME", "").strip()
        hermes_home = (
            Path(configured_home).expanduser()
            if configured_home
            else Path.home() / ".hermes"
        )
    return hermes_home / "state" / "hermes-exchange" / "config.yaml"


def _mapping(value: object, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ExchangeConfigError(f"{field_name} must be a mapping")
    return value


def _only_keys(values: Mapping[str, Any], allowed: set[str], field_name: str) -> None:
    unknown = {key for key in values if not isinstance(key, str) or key not in allowed}
    if unknown:
        rendered = ", ".join(sorted(map(str, unknown)))
        raise ExchangeConfigError(f"{field_name} contains unknown fields: {rendered}")


def _require_int(
    value: object,
    field_name: str,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ExchangeConfigError(f"{field_name} must be an integer")
    if minimum is not None and value < minimum:
        raise ExchangeConfigError(f"{field_name} must be at least {minimum}")
    if maximum is not None and value > maximum:
        raise ExchangeConfigError(f"{field_name} must be at most {maximum}")
    return value


def _require_bool(value: object, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ExchangeConfigError(f"{field_name} must be a boolean")
    return value


def _require_slug(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not _SLUG_RE.fullmatch(value):
        raise ExchangeConfigError(f"{field_name} must be a lowercase slug")
    return value


def _parse_owner(raw: object) -> int:
    owner = _mapping(raw, "owner")
    _only_keys(owner, {"telegram_chat_id"}, "owner")
    owner_chat_id = _require_int(owner.get("telegram_chat_id"), "owner.telegram_chat_id")
    if owner_chat_id == 0:
        raise ExchangeConfigError("owner.telegram_chat_id cannot be zero")
    return owner_chat_id


def _parse_peers(raw: object, local_peer: str) -> dict[str, PeerConfig]:
    raw_peers = _mapping(raw, "peers")
    peers: dict[str, PeerConfig] = {}
    sender_ids: set[int] = set()
    usernames: set[str] = set()
    for raw_name, raw_peer in raw_peers.items():
        name = _require_slug(raw_name, "peer name")
        if name == local_peer:
            raise ExchangeConfigError("local_peer cannot also be a remote peer")
        values = _mapping(raw_peer, f"peers.{name}")
        _only_keys(
            values,
            {"telegram_username", "expected_sender_id", "enabled"},
            f"peers.{name}",
        )
        username = values.get("telegram_username")
        if not isinstance(username, str) or not _TELEGRAM_USERNAME_RE.fullmatch(username):
            raise ExchangeConfigError(
                f"peers.{name}.telegram_username must be a valid @username"
            )
        normalized_username = username.casefold()
        if normalized_username in usernames:
            raise ExchangeConfigError("peer telegram_username values must be unique")
        usernames.add(normalized_username)
        sender_id = _require_int(
            values.get("expected_sender_id"),
            f"peers.{name}.expected_sender_id",
            minimum=1,
        )
        if sender_id in sender_ids:
            raise ExchangeConfigError("peer expected_sender_id values must be unique")
        sender_ids.add(sender_id)
        enabled = _require_bool(values.get("enabled", True), f"peers.{name}.enabled")
        peers[name] = PeerConfig(name, username, sender_id, enabled)
    return peers


def _parse_receive(raw: object) -> ReceiveConfig:
    values = _mapping(raw, "receive")
    _only_keys(values, {"max_message_bytes", "min_peer_interval_seconds"}, "receive")
    return ReceiveConfig(
        max_message_bytes=_require_int(
            values.get("max_message_bytes", 3_500),
            "receive.max_message_bytes",
            minimum=256,
            maximum=3_500,
        ),
        min_peer_interval_seconds=_require_int(
            values.get("min_peer_interval_seconds", 3),
            "receive.min_peer_interval_seconds",
            minimum=1,
            maximum=3_600,
        ),
    )


def _parse_repository(alias: str, raw: object) -> RepositoryConfig:
    values = _mapping(raw, f"execution.repositories.{alias}")
    _only_keys(values, {"path", "executor"}, f"execution.repositories.{alias}")
    raw_path = values.get("path")
    if not isinstance(raw_path, str) or not raw_path:
        raise ExchangeConfigError(f"execution.repositories.{alias}.path must be a path")
    expanded_path = Path(raw_path).expanduser()
    if not expanded_path.is_absolute():
        raise ExchangeConfigError(
            f"execution.repositories.{alias}.path must be absolute"
        )
    try:
        resolved_path = expanded_path.resolve(strict=True)
    except OSError as exc:
        raise ExchangeConfigError(
            f"execution.repositories.{alias}.path does not exist"
        ) from exc
    if not resolved_path.is_dir():
        raise ExchangeConfigError(
            f"execution.repositories.{alias}.path must be a directory"
        )
    executor = values.get("executor")
    if executor not in _EXECUTORS:
        raise ExchangeConfigError(
            f"execution.repositories.{alias}.executor must be claude or codex"
        )
    return RepositoryConfig(alias=alias, path=resolved_path, executor=executor)


def _parse_execution(raw: object) -> ExecutionConfig:
    values = _mapping(raw, "execution")
    _only_keys(
        values,
        {"enabled", "timeout_seconds", "output_limit_chars", "repositories"},
        "execution",
    )
    enabled = _require_bool(values.get("enabled", False), "execution.enabled")
    raw_repositories = _mapping(values.get("repositories", {}), "execution.repositories")
    repositories: dict[str, RepositoryConfig] = {}
    for raw_alias, raw_repository in raw_repositories.items():
        alias = _require_slug(raw_alias, "repository alias")
        repositories[alias] = _parse_repository(alias, raw_repository)
    if enabled and not repositories:
        raise ExchangeConfigError(
            "execution.repositories must contain at least one repository when enabled"
        )
    if not enabled and repositories:
        raise ExchangeConfigError(
            "execution.repositories must be empty when execution is disabled"
        )
    return ExecutionConfig(
        enabled=enabled,
        timeout_seconds=_require_int(
            values.get("timeout_seconds", 1_800),
            "execution.timeout_seconds",
            minimum=1,
            maximum=3_600,
        ),
        output_limit_chars=_require_int(
            values.get("output_limit_chars", 20_000),
            "execution.output_limit_chars",
            minimum=1,
            maximum=100_000,
        ),
        repositories=repositories,
    )


def config_from_mapping(raw: Mapping[str, Any]) -> ExchangeConfig:
    values = _mapping(raw, "config")
    _only_keys(values, {"local_peer", "owner", "peers", "receive", "execution"}, "config")
    local_peer = _require_slug(values.get("local_peer"), "local_peer")
    owner_chat_id = _parse_owner(values.get("owner"))
    peers = _parse_peers(values.get("peers"), local_peer)
    receive = _parse_receive(values.get("receive", {}))
    execution = _parse_execution(values.get("execution", {}))
    return ExchangeConfig(
        local_peer=local_peer,
        owner_chat_id=owner_chat_id,
        peers=peers,
        receive=receive,
        execution=execution,
    )


def load_config(path: str | Path | None = None) -> ExchangeConfig:
    config_path = Path(path) if path is not None else default_config_path()
    try:
        text = config_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ExchangeConfigError(f"cannot read relay config: {config_path}") from exc
    try:
        raw = json.loads(text)
    except json.JSONDecodeError:
        try:
            import yaml
        except ImportError as exc:
            raise ExchangeConfigError(
                "relay config must use JSON-compatible YAML when PyYAML is unavailable"
            ) from exc
        try:
            raw = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise ExchangeConfigError(f"invalid relay config: {config_path}") from exc
    return config_from_mapping(_mapping(raw, "config"))
