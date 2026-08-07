"""Deterministic command-line sender used by Claude Code and Codex skills."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Callable, Mapping, Sequence
import json
import os
import sys
from typing import Any, TextIO

from .config import load_config
from .runtime import RelayRuntime
from .transport import TelegramTransport


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hermes-relay")
    commands = parser.add_subparsers(dest="command", required=True)
    notify = commands.add_parser("notify", help="send one configured-peer notification")
    notify.add_argument("--config")
    notify.add_argument("--peer", required=True)
    notify.add_argument("--kind", default="notice")
    notify.add_argument("--subject", required=True)
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    environ: Mapping[str, str] | None = None,
    config_loader: Callable[[str | None], Any] = load_config,
    transport_factory: Callable[..., Any] = TelegramTransport,
) -> int:
    """Run the sender and emit one sanitized JSON result."""

    values = _parser().parse_args(argv)
    input_stream = stdin or sys.stdin
    output_stream = stdout or sys.stdout
    environment = environ if environ is not None else os.environ
    token = environment.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        _write(
            output_stream,
            {
                "status": "error",
                "error_code": "missing_bot_token",
                "error_message": "TELEGRAM_BOT_TOKEN is not configured.",
            },
        )
        return 2
    try:
        config = config_loader(values.config)
        runtime = RelayRuntime(
            config=config,
            transport=transport_factory(token=token),
        )
        result_text = asyncio.run(
            runtime.notify_tool(
                {
                    "peer": values.peer,
                    "kind": values.kind,
                    "subject": values.subject,
                    "body": input_stream.read(3_001),
                }
            )
        )
        result = json.loads(result_text)
    except Exception:
        result = {
            "status": "error",
            "error_code": "relay_cli_failed",
            "error_message": "The notification could not be prepared or delivered.",
        }
    _write(output_stream, result)
    return 0 if result.get("success") is True else 2


def _write(output: TextIO, payload: Mapping[str, Any]) -> None:
    output.write(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str))
    output.write("\n")


if __name__ == "__main__":
    raise SystemExit(main())
