const ref = prefix => ({ type: 'string', pattern: `^${prefix}:[0-9a-f]{24}$` });
const exactChat = {
  chatName: { type: 'string', minLength: 1, maxLength: 200 },
  chatType: { type: 'string', enum: ['direct', 'group'] },
  chatRef: ref('chat'),
};
const object = properties => ({ type: 'object', properties,
  required: Object.keys(properties), additionalProperties: false });
const operation = { operationId: { type: 'string', pattern: '^forward_[0-9a-f]{32}$' } };
const annotations = readOnlyHint => ({ readOnlyHint, destructiveHint: false,
  idempotentHint: true, openWorldHint: true });
const tool = (name, description, properties, readOnly = false) => ({
  name, description, inputSchema: object(properties), annotations: annotations(readOnly),
});

export const LINE_FORWARD_TOOL_DESCRIPTORS = [
  tool('prepare_line_forward', 'Prepare one original LINE message or attachment for native Share to one exact recipient. Bind accountRef from reader ownSenderRef and source/recipient opaque refs. Automatically locates source and recipient; never clicks final Share. PREPARED requires review of returned source and recipient before confirm. Keep this dedicated MCP connection alive. Reuse idempotencyKey after errors; an earlier dispatch only returns read-only verification. No text-copy or reupload fallback.', {
    accountRef: ref('sender'),
    source: object({ ...exactChat, sourceRef: ref('message'),
      date: { type: 'string', pattern: '^\\d{4}-\\d{2}-\\d{2}$' },
      contentType: { type: 'integer', enum: [0, 1, 2, 3, 14] } }),
    recipient: object(exactChat),
    idempotencyKey: { type: 'string', minLength: 1, maxLength: 160 },
  }),
  tool('confirm_line_forward', 'After user approval of the exact server review, click native Share at most once. Requires the same live process and unexpired preparation. Rechecks UI and local binding, journals dispatch intent before input. UNCERTAIN must only be verified, never resent with a new key. RECORDED_LOCAL proves a matching local record, not delivery or attachment byte identity.', {
    ...operation, preparationId: { type: 'string', minLength: 1, maxLength: 256 },
    reviewDigest: { type: 'string', pattern: '^[0-9a-f]{64}$' }, confirmed: { const: true },
  }),
  tool('verify_line_forward', 'Read-only verification of an existing forward operation, including after restart. Returns binding and receipt strength/limits; never clicks Share or retries sending.', operation, true),
  tool('cancel_line_forward', 'Invalidate an un-dispatched forward preparation and close its proven recipient dialog when possible. Cannot recall or reset dispatch intent; guiMayRemainOpen reports an unclosed dialog.', operation),
];
