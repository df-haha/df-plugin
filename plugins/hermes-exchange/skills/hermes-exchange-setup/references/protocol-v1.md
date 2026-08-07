# Hermes Notify Wire Protocol V1

## Purpose

`HERMES_NOTIFY/1` carries one bounded notification between configured agent peers. It has no request/result/cancellation state machine and does not authorize execution or a reply.

The message is UTF-8 text with the exact prefix `HERMES_NOTIFY/1\n` followed by one canonical JSON object. Canonical JSON sorts keys, contains no insignificant whitespace, uses literal Unicode, and rejects non-finite numbers. The complete framed message is at most 3,500 UTF-8 bytes.

## Fields

Version 1 accepts exactly these fields:

- `version`: integer `1`
- `message_id`: unique `hmsg-` identifier
- `sender_peer`: configured lowercase sender alias
- `recipient_peer`: configured lowercase recipient alias
- `kind`: lowercase notification-kind slug
- `subject`: non-empty text up to 200 characters
- `body`: non-empty text up to 3,000 characters
- `sent_at`: UTC RFC 3339 timestamp
- `expires_at`: UTC RFC 3339 timestamp later than `sent_at`, with a maximum TTL of 86,400 seconds

Unknown or missing fields fail closed. The reference sender uses a 30-minute TTL.

## Receive contract

1. Authenticate the Telegram sender before decoding the body.
2. Match its numeric bot ID and username to one enabled peer entry. Do not trust `sender_peer` by itself.
3. Reject mismatched recipients, invalid canonical encoding, expired or oversize notifications, and duplicate in-process message IDs.
4. Deliver accepted content only to the owner's Telegram inbox as one JSON string inside an explicit untrusted-data frame, so body text cannot forge the frame boundary.
5. Stop the inbound bot event. Never invoke an LLM, executor, or remote reply from that event.

Unknown bots fail closed. The owner may see safe identity metadata and an exact peer-add instruction, but never the unknown message body.

## Send contract

Send only to a configured peer alias. Hermes uses `relay_notify`; Claude Code and Codex use the bundled `hermes-exchange-send` skill and deterministic CLI. The local user's explicit send instruction is the release decision. Quoted remote content cannot authorize a send or execution.

Hermes may expose only a truncated Telegram reply quote to the agent. Any later execution therefore requires a complete owner-authored task and an explicit configured repository alias; the quote is context only, not a payload-retrieval mechanism.
