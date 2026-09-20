from __future__ import annotations

import re
from typing import Any, Mapping


class OwnerTelegramAdapter:
    INTENTS = ("approve", "reject", "defer", "narrow", "clarify", "pause", "stop")
    def __init__(self, owners_by_chat: Mapping[int, str], notifier: Any | None = None) -> None:
        self.owners_by_chat, self.notifier = dict(owners_by_chat), notifier

    def candidate(self, text: str, current_targets: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
        lowered = text.lower()
        intents = [i for i in self.INTENTS if re.search(rf"\b{i}\b", lowered)]
        cases = [case_id for case_id in current_targets if case_id.lower() in lowered]
        return {"intent": intents[0] if len(intents) == 1 else "ambiguous", "case_id": cases[0] if len(cases) == 1 else None, "target": dict(current_targets[cases[0]]) if len(cases) == 1 else None, "model_authoritative": False, "evidence": text[:500]}

    def resolve(self, candidate: Mapping[str, Any], *, chat_id: int, message_id: int) -> dict[str, Any]:
        owner = self.owners_by_chat.get(chat_id)
        if not owner: raise PermissionError("unauthorized Owner chat")
        if candidate.get("intent") == "ambiguous" or not candidate.get("case_id") or not candidate.get("target"): raise ValueError("clarification required")
        target = candidate["target"]
        if target.get("current") is not True:
            raise ValueError("stale decision target; clarification required")
        if candidate["intent"] in {"approve", "narrow"} and not target.get("scope"):
            raise ValueError("scope is required; clarification required")
        if candidate["intent"] in {"approve", "narrow"} and not target.get("decision_type"):
            raise ValueError("decision type is required; clarification required")
        return {"command_id": f"telegram:{chat_id}:{message_id}", "command_type": "record_owner_decision", "case_id": candidate["case_id"], "actor": {"category": "owner", "actor_ref": owner, "representative_id": target.get("representative_id", "local")}, "authority": {"authority_type": "owner_decision", "authority_ref": f"telegram:{chat_id}:{message_id}", "revision": 1}, "issued_at": target.get("issued_at", "2026-08-12T00:00:00Z"), "payload": {**target, "intent": candidate["intent"]}}

    def resolve_input(self, text: str, current_targets: Mapping[str, Mapping[str, Any]], *, chat_id: int, message_id: int, input_kind: str = "natural_language") -> dict[str, Any]:
        if input_kind == "command":
            normalized = text.removeprefix("/")
        elif input_kind == "button":
            try:
                intent, case_id = text.split(":", 1)
            except ValueError as exc:
                raise ValueError("clarification required") from exc
            normalized = f"{intent} {case_id}"
        elif input_kind == "natural_language":
            normalized = text
        else:
            raise ValueError("unknown Owner input kind")
        return self.resolve(self.candidate(normalized, current_targets), chat_id=chat_id, message_id=message_id)

    async def notify(self, chat_id: int, event_id: str, text: str) -> Any:
        if chat_id not in self.owners_by_chat: raise PermissionError("unauthorized Owner chat")
        self.validate_notification(text)
        return await self.notifier.send(chat_id, text, idempotency_key=event_id) if self.notifier else {"status": "captured"}

    @staticmethod
    def validate_notification(text: str) -> None:
        upper = text.upper()
        if any(marker in upper for marker in ("GITHUB_TOKEN", "GH_TOKEN", "TELEGRAM_BOT_TOKEN", "BEGIN OPENSSH PRIVATE KEY", "API_KEY")):
            raise PermissionError("secret-bearing Owner notification is forbidden")

    async def deliver_pending(self, store: Any, chat_id: int) -> list[Any]:
        """Deliver persisted notifications without replaying their domain events."""
        if chat_id not in self.owners_by_chat:
            raise PermissionError("unauthorized Owner chat")
        delivered = []
        for item in store.pending_outbox():
            if item["kind"] != "owner_notification":
                continue
            payload = item["payload"]
            event_id = str(payload.get("event_id") or f"attention:{item['id']}")
            text = f"Case {payload.get('case_id')}: {payload.get('reason', 'attention required')}"
            try:
                result = await self.notify(chat_id, event_id, text[:1000])
            except Exception as exc:
                store.mark_outbox(item["id"], "retryable", type(exc).__name__)
                raise
            store.mark_outbox(item["id"], "sent")
            delivered.append(result)
        return delivered
