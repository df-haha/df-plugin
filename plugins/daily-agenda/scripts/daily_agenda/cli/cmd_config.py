"""config get / config set KEY=VALUE..."""

from __future__ import annotations

from daily_agenda import state
from ._common import Deps, emit

_ALLOWED_KEYS = {
    "output.dir", "output.formats",
    "refine.every_runs", "refine.every_days",
    "analysis.model", "analysis.timeout_s", "analysis.batch_bytes",
    "line.enabled", "mail.enabled",
    "tz",
}

_INT_KEYS = {"refine.every_runs", "refine.every_days", "analysis.timeout_s", "analysis.batch_bytes"}
_BOOL_KEYS = {"line.enabled", "mail.enabled"}
_LIST_KEYS = {"output.formats"}
_VALID_FORMATS = {"html", "md"}


def _set_nested(cfg: dict, dotkey: str, value: object) -> None:
    parts = dotkey.split(".")
    obj = cfg
    for part in parts[:-1]:
        obj = obj.setdefault(part, {})
    obj[parts[-1]] = value


def _get_nested(cfg: dict, dotkey: str) -> object:
    parts = dotkey.split(".")
    obj = cfg
    for part in parts:
        if isinstance(obj, dict):
            obj = obj.get(part)
        else:
            return None
    return obj


def handle(argv: list[str], deps: Deps) -> int:
    if not argv:
        return emit({"ok": False, "code": "ARG_ERROR", "detail": "usage: config get|set"}, exit_code=2)

    sub = argv[0]
    cfg = state.load_config(deps.home)

    if sub == "get":
        # Return a summary of all config (no raw content)
        summary: dict = {}
        for key in sorted(_ALLOWED_KEYS):
            summary[key] = _get_nested(cfg, key)
        return emit({"ok": True, "code": None, "config": summary})

    elif sub == "set":
        pairs = argv[1:]
        if not pairs:
            return emit({"ok": False, "code": "ARG_ERROR", "detail": "config set KEY=VALUE..."}, exit_code=2)

        for pair in pairs:
            if "=" not in pair:
                return emit({"ok": False, "code": "ARG_ERROR", "detail": f"invalid pair: {pair}"}, exit_code=2)
            key, _, raw_value = pair.partition("=")
            if key not in _ALLOWED_KEYS:
                return emit({"ok": False, "code": "ARG_ERROR", "detail": f"unknown config key: {key}"}, exit_code=2)

            if key in _INT_KEYS:
                try:
                    value: object = int(raw_value)
                except ValueError:
                    return emit({"ok": False, "code": "ARG_ERROR", "detail": f"{key} must be integer"}, exit_code=2)
            elif key in _BOOL_KEYS:
                value = raw_value.lower() in ("true", "1", "yes")
            elif key in _LIST_KEYS:
                parts = [v.strip() for v in raw_value.split(",") if v.strip()]
                if key == "output.formats":
                    invalid = set(parts) - _VALID_FORMATS
                    if invalid:
                        return emit({"ok": False, "code": "ARG_ERROR",
                                      "detail": f"invalid formats: {invalid}; allowed: {_VALID_FORMATS}"}, exit_code=2)
                value = parts
            else:
                value = raw_value if raw_value else None

            _set_nested(cfg, key, value)

        state.save_config(deps.home, cfg)
        return emit({"ok": True, "code": None})

    else:
        return emit({"ok": False, "code": "ARG_ERROR", "detail": "usage: config get|set"}, exit_code=2)
