from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml


class CollaborationConfigError(ValueError):
    pass


def _contains_forbidden_secret_key(value: Any) -> bool:
    if isinstance(value, Mapping):
        return any(
            "token" in str(key).lower()
            or "secret" in str(key).lower()
            or _contains_forbidden_secret_key(item)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_forbidden_secret_key(item) for item in value)
    return False


@dataclass(frozen=True, slots=True)
class PeerConfig:
    representative_id: str
    telegram_username: str
    numeric_bot_id: int
    revision: int
    active: bool = True


@dataclass(frozen=True, slots=True)
class CollaborationConfig:
    representative_id: str
    owner_chat_id: int
    owner_ids: tuple[str, ...]
    peers: Mapping[str, PeerConfig]
    executor_registrations: Mapping[str, int]
    authorized_repositories: tuple[str, ...]
    intake_agents: tuple[str, ...]
    max_message_bytes: int = 3500
    ttl_seconds: int = 1800
    min_send_interval_seconds: int = 3


def default_config_path(home: Path) -> Path:
    return home / "state" / "hermes-collaboration" / "config.yaml"


def load_config(path: str | Path) -> CollaborationConfig:
    config_path = Path(path)
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise CollaborationConfigError(f"cannot read collaboration config: {config_path}") from exc
    if not isinstance(raw, Mapping):
        raise CollaborationConfigError("config must be a mapping")
    if _contains_forbidden_secret_key(raw):
        raise CollaborationConfigError("secrets and tokens do not belong in collaboration config")
    representative_id = raw.get("representative_id")
    owner = raw.get("owner")
    if not isinstance(representative_id, str) or not representative_id or not isinstance(owner, Mapping):
        raise CollaborationConfigError("representative_id and owner are required")
    owner_chat_id = owner.get("telegram_chat_id")
    owner_ids = owner.get("owner_ids")
    if not isinstance(owner_chat_id, int) or not isinstance(owner_ids, list) or not owner_ids:
        raise CollaborationConfigError("owner telegram_chat_id and owner_ids are required")
    raw_peers = raw.get("peers")
    if not isinstance(raw_peers, Mapping) or not raw_peers:
        raise CollaborationConfigError("at least one PeerTrust is required")
    peers: dict[str, PeerConfig] = {}
    bot_ids: set[int] = set()
    for peer_id, values in raw_peers.items():
        if not isinstance(values, Mapping):
            raise CollaborationConfigError("PeerTrust must be a mapping")
        username, bot_id, revision = values.get("telegram_username"), values.get("numeric_bot_id"), values.get("revision")
        if not isinstance(peer_id, str) or not peer_id or not isinstance(username, str) or not username.startswith("@") or not isinstance(bot_id, int) or bot_id < 1 or bot_id in bot_ids or not isinstance(revision, int) or revision < 1:
            raise CollaborationConfigError("invalid or ambiguous PeerTrust")
        bot_ids.add(bot_id)
        peers[peer_id] = PeerConfig(peer_id, username, bot_id, revision, bool(values.get("active", True)))
    raw_executors = raw.get("executor_registrations", {})
    if not isinstance(raw_executors, Mapping) or not raw_executors or any(not isinstance(key, str) or not isinstance(value, int) or value < 1 for key, value in raw_executors.items()):
        raise CollaborationConfigError("at least one valid ExecutorRegistration is required")
    policy = raw.get("transport", {})
    if not isinstance(policy, Mapping):
        raise CollaborationConfigError("transport must be a mapping")
    max_bytes = int(policy.get("max_message_bytes", 3500))
    ttl = int(policy.get("ttl_seconds", 1800))
    interval = int(policy.get("min_send_interval_seconds", 3))
    if not 256 <= max_bytes <= 3500 or ttl < 1 or interval < 0:
        raise CollaborationConfigError("unsafe transport bounds")
    return CollaborationConfig(
        representative_id=representative_id,
        owner_chat_id=owner_chat_id,
        owner_ids=tuple(str(item) for item in owner_ids),
        peers=peers,
        executor_registrations=dict(raw_executors),
        authorized_repositories=tuple(str(item) for item in raw.get("authorized_repositories", [])),
        intake_agents=tuple(str(item) for item in raw.get("intake_agents", [])),
        max_message_bytes=max_bytes,
        ttl_seconds=ttl,
        min_send_interval_seconds=interval,
    )
