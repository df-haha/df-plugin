# Adapter Guide

Use this guide when the peer is not running the bundled Hermes plugin.

## Minimal wire-compatible adapter

1. Receive a transport message together with an authenticated sender identifier.
2. Detect the exact `HERMES_EXCHANGE/1\n` prefix.
3. Decode and validate the envelope using `protocol-v1.md`.
4. Match the authenticated sender identifier and `recipient_peer` against local configuration.
5. Persist the exact message hash before dispatching work.
6. Show a plain-language summary and recommended action to the local owner.
7. On local acceptance, execute in the runtime's own bounded/captured environment.
8. Capture the result locally and require return approval.
9. Encode `result`, `rejection`, or `cancellation` with the same `exchange_id` and the request's `envelope_id` as `in_reply_to`.

## Framework seams

Keep these replaceable:

- `Transport.send(target, text) -> DeliveryResult`
- `IdentityResolver(transport_event) -> authenticated_peer_id`
- `EnvelopeCodec.encode/decode`
- `ExchangeStore` for replay, state, attempts, and audit
- `OwnerGate` for send/accept/return decisions
- `Executor` for the local agent or CLI runtime

The bundled implementation uses Telegram, SQLite, Hermes hooks, and a Hermes LLM turn. Another implementation may use PostgreSQL, a webhook, Claude Code, Codex, or a queue while preserving the same wire and safety semantics.

## Conformance check

At minimum, test:

- the canonical request fixture decodes without importing Hermes;
- unknown version and recipient mismatch fail closed;
- expired and oversize envelopes fail closed;
- exact replay is a no-op and conflicting replay is rejected;
- result/rejection/cancellation require correct correlation;
- an accepted task cannot send outward before local result approval;
- retry and loop bounds survive restart.

Do not label an adapter workflow-safe until every item is exercised against its real runtime boundary.
