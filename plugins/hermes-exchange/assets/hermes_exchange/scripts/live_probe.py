#!/usr/bin/env python3
"""One-shot, human-gated live probe for two configured Hermes bots."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
import time

import httpx

if __package__:
    from ..config import ExchangeConfigError
    from ..workflow import ExchangeRuntime
else:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from hermes_exchange.config import ExchangeConfigError
    from hermes_exchange.workflow import ExchangeRuntime


async def _get_me(token: str) -> dict:
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"https://api.telegram.org/bot{token}/getMe"
            )
        body = response.json()
    except Exception as exc:
        raise RuntimeError("Telegram getMe failed") from exc
    if response.status_code != 200 or not isinstance(body, dict) or body.get("ok") is not True:
        raise RuntimeError("Telegram getMe rejected the configured bot token")
    result = body.get("result")
    if not isinstance(result, dict) or not result.get("id"):
        raise RuntimeError("Telegram getMe returned an invalid bot identity")
    return result


async def run(peer: str, timeout_seconds: int) -> int:
    runtime = ExchangeRuntime.from_user_scope(llm=None)
    if runtime.config is None or runtime.store is None:
        raise ExchangeConfigError(runtime.config_error or "exchange config unavailable")
    configured_peer = runtime.config.peer(peer)
    identity = await _get_me(os.getenv("TELEGRAM_BOT_TOKEN", ""))
    print(
        "Local bot identity verified: "
        f"id={identity['id']} username=@{identity.get('username', '<unknown>')}"
    )
    print(
        "Configured peer: "
        f"name={configured_peer.name} username={configured_peer.telegram_username} "
        f"expected_sender_id={configured_peer.expected_sender_id}"
    )

    prepared = json.loads(
        runtime.prepare_tool(
            {
                "peer": configured_peer.name,
                "kind": "probe",
                "subject": "Hermes Exchange one-shot live probe",
                "summary": "Return a harmless confirmation containing LIVE_PROBE_OK.",
                "payload": (
                    "This is a transport and human-gate probe. Do not call tools or "
                    "change files. Return only LIVE_PROBE_OK to your owner for approval."
                ),
                "constraints": [
                    "No tools",
                    "No file changes",
                    "No network calls except the approved result return",
                ],
                "artifact_refs": [],
            }
        )
    )
    if prepared.get("status") != "prepared":
        raise RuntimeError("Could not prepare the live probe draft")
    exchange_id = str(prepared["exchange_id"])
    print(f"Probe draft prepared: {exchange_id}")
    print(f"Human gate required in Telegram: /exchange send {exchange_id}")
    print("The recipient must accept it and approve returning the result.")

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        exchange = runtime.store.get_exchange(exchange_id)
        state = exchange["state"]
        if state == "closed" and exchange.get("latest_result"):
            result = str(exchange.get("latest_result") or "")
            if "LIVE_PROBE_OK" not in result:
                raise RuntimeError("Probe result returned without LIVE_PROBE_OK")
            print(f"PASS: one request/result pair completed for {exchange_id}")
            return 0
        if state in {
            "rejected",
            "cancelled",
            "expired",
            "delivery_failed",
            "security_rejected",
        }:
            raise RuntimeError(f"Probe ended in terminal state: {state}")
        await asyncio.sleep(2)
    raise RuntimeError("Probe timed out before an approved result returned")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run one explicitly configured, human-gated Hermes Exchange probe."
    )
    parser.add_argument("--peer", required=True, help="Configured peer name; never guessed.")
    parser.add_argument("--timeout-seconds", type=int, default=300)
    args = parser.parse_args()
    if args.timeout_seconds < 10 or args.timeout_seconds > 3600:
        parser.error("--timeout-seconds must be between 10 and 3600")
    try:
        return asyncio.run(run(args.peer, args.timeout_seconds))
    except (ExchangeConfigError, RuntimeError) as exc:
        print(f"NOT_RUN/FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
