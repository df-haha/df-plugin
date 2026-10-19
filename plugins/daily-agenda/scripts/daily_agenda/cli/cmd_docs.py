"""docs convert command — convert user documents for profile building."""

from __future__ import annotations

import secrets
from pathlib import Path

from ._common import Deps, emit, require_consent
from daily_agenda import state


def handle(argv: list[str], deps: Deps) -> int:
    if not argv or argv[0] != "convert":
        return emit({"ok": False, "code": "ARG_ERROR",
                      "detail": 'usage: docs convert --path "<p>" [--path ...]'},
                    exit_code=2)

    cfg = state.load_config(deps.home)
    rc = require_consent(cfg)
    if rc is not None:
        return rc

    # Parse --path arguments
    paths: list[Path] = []
    i = 1
    while i < len(argv):
        if argv[i] == "--path" and i + 1 < len(argv):
            paths.append(Path(argv[i + 1]))
            i += 2
        else:
            i += 1

    if not paths:
        return emit({"ok": False, "code": "ARG_ERROR",
                      "detail": 'usage: docs convert --path "<p>" [--path ...]'},
                    exit_code=2)

    # Validate paths exist
    for p in paths:
        if not p.exists():
            return emit({"ok": False, "code": "ARG_ERROR",
                          "detail": f"path not found: {p}"}, exit_code=2)

    # Build forbidden prefixes (dirs containing raw third-party data)
    forbidden: list[Path] = [
        deps.home / "runs",
        deps.home / "gen",
        deps.home / "output",
    ]
    output_dir_cfg = cfg.get("output", {}).get("dir")
    if output_dir_cfg:
        forbidden.append(Path(output_dir_cfg))

    # Generate batch ID and output dir under docs-cache (NOT runs/)
    batch_id = secrets.token_hex(4)
    output_dir = deps.home / "docs-cache" / batch_id

    from daily_agenda.docs_convert import convert_paths
    result = convert_paths(paths, output_dir, forbidden_prefixes=forbidden)
    return emit(result)
