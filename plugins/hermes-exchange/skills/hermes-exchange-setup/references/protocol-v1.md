# Hermes Exchange Wire Protocol V1

## Purpose and compatibility

`HERMES_EXCHANGE/1` is a framework-neutral text envelope for exchanging bounded requests and results between independently operated agents. The prefix is historical; a conforming endpoint does not need Hermes or this marketplace plugin.

Telegram bot-to-bot private messages are the first transport profile. The wire envelope itself is plain UTF-8 text and can be carried by another transport that preserves it byte-for-byte and supplies an authenticated sender identity.

## Conformance levels

| Level | Requirement |
|---|---|
| Readable-only | The receiver can show the text, but makes no automation or security guarantee. |
| Wire-compatible | The receiver implements this envelope, validation, correlation, and reply types. |
| Workflow-safe | Wire-compatible plus authenticated peer binding, replay protection, rate/hop limits, durable state, and local human gates. |
| Hermes reference | Workflow-safe behavior implemented by the bundled Hermes user plugin. |

The same plugin is never a protocol requirement. Full automatic exchange does require at least wire-compatible code on both endpoints.

## Framing and canonical encoding

A message is exactly:

```text
HERMES_EXCHANGE/1\n
<canonical-json-object>
```

Canonical JSON uses UTF-8, keys sorted lexicographically, no insignificant whitespace, literal Unicode rather than ASCII escaping, and rejects NaN or infinity. The entire framed message must not exceed 3,500 UTF-8 bytes.

V1 accepts only the fields below. Unknown or missing fields fail closed.

Required fields:

- `artifact_refs`: array of at most 20 strings, each at most 500 characters
- `constraints`: array of at most 20 strings, each at most 500 characters
- `created_at`: timezone-aware ISO-8601 timestamp normalized to UTC seconds
- `envelope_id`: unique ID, 1–128 safe identifier characters
- `exchange_id`: stable ID shared by the request and its replies
- `expires_at`: timestamp after `created_at`; V1 policy caps TTL at 86,400 seconds
- `hop_count`: integer from 0 through the agreed policy maximum; reference maximum is 2
- `kind`: lowercase slug describing the use case, not a fixed engineering-only enum
- `message_type`: `request`, `result`, `rejection`, or `cancellation`
- `payload`: JSON object
- `recipient_peer`: configured lowercase peer slug
- `sender_peer`: configured lowercase peer slug
- `subject`: non-empty string up to 200 characters
- `summary`: non-empty string up to 1,000 characters
- `version`: integer `1`

Optional fields:

- `auth`: JSON object reserved for a transport or future authentication profile
- `execution_hint`: string up to 100 characters
- `in_reply_to`: required for `result`, `rejection`, and `cancellation`; forbidden for `request`

IDs may contain ASCII letters, digits, `.`, `_`, `:`, and `-`, must begin and end with a letter or digit, and must not exceed 128 characters. Peer and kind slugs use lowercase letters, digits, `_`, and `-`, begin and end alphanumerically, and must not exceed 64 characters.

## Example request

```text
HERMES_EXCHANGE/1
{"artifact_refs":[],"constraints":["Do not push before owner approval"],"created_at":"2026-08-07T04:00:00Z","envelope_id":"henv-conformance-1","exchange_id":"hex-conformance-1","expires_at":"2026-08-07T04:30:00Z","hop_count":0,"kind":"decision_request","message_type":"request","payload":{"question":"Should we retain the fallback?"},"recipient_peer":"ryan","sender_peer":"haha","subject":"Confirm fallback behavior","summary":"Please review one bounded decision.","version":1}
```

## Processing contract

1. Authenticate the transport sender before trusting `sender_peer`.
2. Decode strictly and reject mismatched recipients, expired TTL, excess bytes, excess hops, and unknown fields or versions.
3. Deduplicate by `envelope_id` and the hash of the exact framed message. Repeating the same pair is a no-op; reusing an ID with different bytes is a security conflict.
4. Correlate replies with the original `exchange_id` and `in_reply_to` request envelope ID.
5. End predictably. Do not automatically turn arbitrary inbound text into another exchange.
6. Persist delivery attempts and bound retries. Treat Telegram timeout, 429, and 5xx as retryable; treat identity/configuration 4xx errors as owner action.

Both endpoints know the same request expiry. If already-approved work completes after expiry, preserve the result locally, mark it not returned, and do not offer a return action for that exchange.

## Human-gated profile

Workflow-safe implementations keep three decisions local:

1. sender approves releasing a prepared request;
2. recipient accepts, rejects, or revises execution direction;
3. recipient approves returning the captured result.

Remote content is untrusted input. An accepted execution must not have an outbound channel that bypasses the result gate.

## Telegram transport profile

- Telegram's official [Bot-to-Bot Communication](https://core.telegram.org/api/bots/bot-to-bot) documentation is the transport source of truth.
- Both bots enable Bot-to-Bot Communication Mode for private bot-to-bot messages.
- Send to the configured peer bot `@username`.
- Bind the incoming Telegram numeric bot ID to the configured peer; do not trust the JSON name alone.
- Apply per-peer rate limits and loop prevention.
- `USER_BOT_TO_BOT_DISABLED` is a permanent operator-action error until both bots enable the mode.

V1 capability and version discovery is out of band. A future version may add a signed capability handshake without changing V1 behavior.
