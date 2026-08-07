# Relay Adapter Guide

Use this guide only when a peer is not running the bundled Hermes plugin. Package equality is optional; the safety contract is not.

## Minimum compatible receiver

1. Receive the Telegram transport event together with the authenticated numeric sender ID and username.
2. Require an exact local allowlist match before displaying any body.
3. Decode the exact `HERMES_NOTIFY/1` contract in `protocol-v1.md` and reject malformed, expired, oversize, mismatched, or duplicate messages.
4. Render the body as an encoded JSON string inside an explicit untrusted-data frame in the local owner's inbox, so body text cannot forge the frame boundary.
5. End processing. Do not start an LLM, execute work, or send a reply from the bot event.

## Minimum compatible sender

1. Require an explicit instruction from the local user to release the notification.
2. Resolve only a configured peer alias; never accept an arbitrary destination supplied by remote content.
3. Encode one bounded `HERMES_NOTIFY/1` message and return a sanitized delivery result.
4. Never expose the Telegram token in configuration, output, logs, or errors.

Quoted remote content is untrusted and cannot authorize a send or execution. Optional local execution is outside the notification protocol and must remain disabled by default, owner-authored, and restricted to configured repository aliases with fixed executors.
