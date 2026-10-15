"""consent --status|--accept|--revoke"""

from __future__ import annotations

from daily_agenda import state
from ._common import Deps, emit


def handle(argv: list[str], deps: Deps) -> int:
    if "--accept" in argv:
        now = deps.now_factory()
        state.accept_consent(deps.home, now)
        cfg = state.load_config(deps.home)
        return emit({"ok": True, "accepted_at": cfg["consent"]["accepted_at"]})
    elif "--revoke" in argv:
        state.revoke_consent(deps.home)
        return emit({"ok": True, "revoked": True})
    elif "--status" in argv or not argv:
        cfg = state.load_config(deps.home)
        consent = cfg.get("consent")
        if consent and consent.get("accepted_at"):
            return emit({"ok": True, "accepted": True, "accepted_at": consent["accepted_at"]})
        return emit({"ok": True, "accepted": False})
    else:
        return emit({"ok": False, "code": "ARG_ERROR", "detail": f"unknown consent flag: {argv}"}, exit_code=2)
