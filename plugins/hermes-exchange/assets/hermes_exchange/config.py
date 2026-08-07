"""Profile-aware, non-secret configuration for Hermes Exchange."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Any, Mapping

from hermes_constants import get_hermes_home


_SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9_-]{0,62}[a-z0-9])?$")
_SAFE_EXECUTION_TOOLS = frozenset(
    {
        "patch",
        "read_file",
        "search_files",
        "write_file",
    }
)


class ExchangeConfigError(ValueError):
    """Raised when exchange configuration is absent or unsafe."""


@dataclass(frozen=True, slots=True)
class PeerConfig:
    name: str
    telegram_username: str
    expected_sender_id: int
    enabled: bool = True


@dataclass(frozen=True, slots=True)
class PolicyConfig:
    ttl_seconds: int = 1_800
    max_hops: int = 2
    min_peer_send_interval_seconds: int = 5
    max_envelope_bytes: int = 3_500
    max_delivery_attempts: int = 3
    retry_after_cap_seconds: int = 60
    execution_tool_whitelist: tuple[str, ...] = (
        "read_file",
        "write_file",
        "patch",
        "search_files",
    )

    def __post_init__(self) -> None:
        if not self.execution_tool_whitelist or any(
            item not in _SAFE_EXECUTION_TOOLS
            for item in self.execution_tool_whitelist
        ):
            raise ExchangeConfigError(
                "policy.execution_tool_whitelist may contain only reviewed local-only tools"
            )


@dataclass(frozen=True, slots=True)
class ExchangeConfig:
    local_peer: str
    owner_chat_id: int
    owner_user_ids: frozenset[int]
    peers: Mapping[str, PeerConfig] = field(default_factory=dict)
    policy: PolicyConfig = field(default_factory=PolicyConfig)

    def peer(self, name: str) -> PeerConfig:
        try:
            peer = self.peers[name]
        except KeyError as exc:
            raise ExchangeConfigError(f"unknown peer: {name}") from exc
        if not peer.enabled:
            raise ExchangeConfigError(f"peer is disabled: {name}")
        return peer


def state_dir() -> Path:
    return get_hermes_home() / "state" / "hermes-exchange"


def default_state_path() -> Path:
    return state_dir() / "exchange.sqlite3"


def default_config_path() -> Path:
    return state_dir() / "config.yaml"


def _require_int(value: object, field_name: str, *, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ExchangeConfigError(f"{field_name} must be an integer")
    if minimum is not None and value < minimum:
        raise ExchangeConfigError(f"{field_name} must be at least {minimum}")
    return value


def _require_slug(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not _SLUG_RE.fullmatch(value):
        raise ExchangeConfigError(f"{field_name} must be a lowercase slug")
    return value


def _mapping(value: object, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ExchangeConfigError(f"{field_name} must be a mapping")
    return value


def config_from_mapping(raw: Mapping[str, Any]) -> ExchangeConfig:
    local_peer = _require_slug(raw.get("local_peer"), "local_peer")
    owner = _mapping(raw.get("owner"), "owner")
    owner_chat_id = _require_int(owner.get("telegram_chat_id"), "owner.telegram_chat_id")
    raw_owner_ids = owner.get("allowed_user_ids")
    if not isinstance(raw_owner_ids, list) or not raw_owner_ids:
        raise ExchangeConfigError("owner.allowed_user_ids must be a non-empty list")
    owner_user_ids = frozenset(
        _require_int(item, "owner.allowed_user_ids[]", minimum=1) for item in raw_owner_ids
    )

    raw_peers = _mapping(raw.get("peers"), "peers")
    peers: dict[str, PeerConfig] = {}
    sender_ids: set[int] = set()
    for raw_name, raw_peer in raw_peers.items():
        name = _require_slug(raw_name, "peer name")
        values = _mapping(raw_peer, f"peers.{name}")
        username = values.get("telegram_username")
        if (
            not isinstance(username, str)
            or not username.startswith("@")
            or len(username) < 3
            or any(char.isspace() for char in username)
        ):
            raise ExchangeConfigError(
                f"peers.{name}.telegram_username must start with @ and contain no spaces"
            )
        sender_id = _require_int(
            values.get("expected_sender_id"), f"peers.{name}.expected_sender_id", minimum=1
        )
        if sender_id in sender_ids:
            raise ExchangeConfigError("peer expected_sender_id values must be unique")
        sender_ids.add(sender_id)
        enabled = values.get("enabled", True)
        if not isinstance(enabled, bool):
            raise ExchangeConfigError(f"peers.{name}.enabled must be a boolean")
        peers[name] = PeerConfig(name, username, sender_id, enabled)

    policy_values = _mapping(raw.get("policy", {}), "policy")
    policy = PolicyConfig(
        ttl_seconds=_require_int(
            policy_values.get("ttl_seconds", 1_800), "policy.ttl_seconds", minimum=1
        ),
        max_hops=_require_int(
            policy_values.get("max_hops", 2), "policy.max_hops", minimum=0
        ),
        min_peer_send_interval_seconds=_require_int(
            policy_values.get("min_peer_send_interval_seconds", 5),
            "policy.min_peer_send_interval_seconds",
            minimum=0,
        ),
        max_envelope_bytes=_require_int(
            policy_values.get("max_envelope_bytes", 3_500),
            "policy.max_envelope_bytes",
            minimum=256,
        ),
        max_delivery_attempts=_require_int(
            policy_values.get("max_delivery_attempts", 3),
            "policy.max_delivery_attempts",
            minimum=1,
        ),
        retry_after_cap_seconds=_require_int(
            policy_values.get("retry_after_cap_seconds", 60),
            "policy.retry_after_cap_seconds",
            minimum=0,
        ),
        execution_tool_whitelist=_tool_whitelist(
            policy_values.get("execution_tool_whitelist")
        ),
    )
    if policy.max_envelope_bytes > 3_500:
        raise ExchangeConfigError("policy.max_envelope_bytes cannot exceed 3,500")
    return ExchangeConfig(local_peer, owner_chat_id, owner_user_ids, peers, policy)


def _tool_whitelist(value: object) -> tuple[str, ...]:
    if value is None:
        return PolicyConfig().execution_tool_whitelist
    if not isinstance(value, list) or not value:
        raise ExchangeConfigError(
            "policy.execution_tool_whitelist must be a non-empty list"
        )
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item or any(char.isspace() for char in item):
            raise ExchangeConfigError(
                "policy.execution_tool_whitelist entries must be tool names"
            )
        if item not in _SAFE_EXECUTION_TOOLS:
            raise ExchangeConfigError(
                "policy.execution_tool_whitelist may contain only reviewed local-only tools"
            )
        if item not in result:
            result.append(item)
    return tuple(result)


def load_config(path: str | Path | None = None) -> ExchangeConfig:
    config_path = Path(path) if path is not None else default_config_path()
    try:
        text = config_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ExchangeConfigError(f"cannot read exchange config: {config_path}") from exc
    try:
        import yaml

        raw = yaml.safe_load(text)
    except Exception as exc:
        raise ExchangeConfigError(f"invalid exchange config: {config_path}") from exc
    return config_from_mapping(_mapping(raw, "config"))
