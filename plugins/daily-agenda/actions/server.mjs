#!/usr/bin/env node

/**
 * daily-agenda actions MCP server.
 * stdio JSON-RPC 2.0 (NDJSON). No npm dependencies.
 *
 * Tools: prepare_action, commit_action, action_status.
 *
 * Adapter injection: only DAILY_AGENDA_ACTIONS_TEST_ADAPTER=fake selects the
 * built-in fake adapters. Any other value exits with code 1. Real adapters
 * are the default when the env var is absent.
 */

import path from 'node:path';
import { readFileSync } from 'node:fs';
import { homedir } from 'node:os';
import {
  parseMessage, successResponse, errorResponse,
  METHOD_NOT_FOUND, INVALID_PARAMS, INTERNAL_ERROR, PARSE_ERROR,
  createLineReader,
} from './lib/jsonrpc.mjs';
import { canonicalJson, sha256, sha256Raw } from './lib/canonical.mjs';
import { deriveDisplay, sanitizeContent } from './lib/display.mjs';
import {
  isValidId, newActionId, newNonce, ensureDirs, actionDirs,
  writePending, claimOwnership, readCommitting, finalize, readDone, readPending,
  recoverCommitting, atomicWriteJson,
} from './lib/store.mjs';
import { findSimilarEvents } from './lib/similar.mjs';
import { createFakeLineAdapter, createFakeCalendarAdapter } from './lib/adapters-fake.mjs';

// ── Configuration ──────────────────────────────────────────────

const EXPIRY_MS = 10 * 60 * 1000; // 10 minutes
const MAX_TEXT_LENGTH = 10000;
const CHAT_REF_RE = /^chat:[0-9a-f]{24}$/;

const getHome = () => {
  const env = process.env.DAILY_AGENDA_HOME;
  if (env) return path.resolve(env);
  return path.resolve(homedir(), '.daily-agenda');
};

// ── Tool definitions ───────────────────────────────────────────

const TOOLS = [
  {
    name: 'prepare_action',
    description: 'Prepare a LINE reply or calendar event for user confirmation. Returns a preview with target_display and content_display that must be passed unchanged to commit_action.',
    inputSchema: {
      type: 'object',
      properties: {
        kind: { type: 'string', enum: ['line_reply', 'calendar_event'] },
        target: {
          type: 'object',
          description: 'For line_reply: {chat_ref, chat_name, chat_type}. For calendar_event: {start_iso, end_iso, subject, location?, attendees?}.',
        },
        content: {
          description: 'For line_reply: plain text string. For calendar_event: omitted (target contains all fields).',
        },
        allow_duplicate: { type: 'boolean', description: 'For calendar_event: skip duplicate check if true.' },
      },
      required: ['kind', 'target'],
    },
  },
  {
    name: 'commit_action',
    description: 'Send a previously prepared action after user confirmation. The target_display and content_display must match the prepare response exactly.',
    inputSchema: {
      type: 'object',
      properties: {
        id: { type: 'string', pattern: '^[0-9a-f]{32}$' },
        target_display: { type: 'string' },
        content_display: { type: 'string' },
      },
      required: ['id', 'target_display', 'content_display'],
    },
    _meta: { 'anthropic/requiresUserInteraction': true },
  },
  {
    name: 'action_status',
    description: 'Check the status of a prepared or committed action. For uncertain actions, queries the adapter to determine if it was actually sent.',
    inputSchema: {
      type: 'object',
      properties: {
        id: { type: 'string', pattern: '^[0-9a-f]{32}$' },
      },
      required: ['id'],
    },
  },
];

// ── Server factory ─────────────────────────────────────────────

/**
 * @param {{
 *   home?: string,
 *   lineAdapter?: object,
 *   calendarAdapter?: object,
 *   now?: () => Date,
 *   stderr?: (msg: string) => void,
 * }} options
 */
export const createServer = (options = {}) => {
  const home = options.home || getHome();
  const now = options.now || (() => new Date());
  const stderr = options.stderr || ((msg) => process.stderr.write(`${msg}\n`));
  const lineAdapter = options.lineAdapter || null;
  const calendarAdapter = options.calendarAdapter || null;

  let initialized = false;

  // ── prepare_action ──

  const handlePrepare = async (params) => {
    const { kind, target, allow_duplicate } = params;

    if (kind === 'line_reply') {
      return prepareLineReply(params);
    }
    if (kind === 'calendar_event') {
      return prepareCalendarEvent(target, allow_duplicate);
    }
    return { isError: true, content: [{ type: 'text', text: `Unknown kind: ${kind}` }] };
  };

  /** F7: resolve chat display name from state.json known_chats by ref. */
  const resolveKnownChatName = (chatRef) => {
    try {
      const statePath = path.join(home, 'gen', String(_currentGen()), 'state.json');
      const raw = readFileSync(statePath, 'utf8');
      const st = JSON.parse(raw);
      for (const c of (st.known_chats || [])) {
        if (c.ref === chatRef) return c.name || null;
      }
    } catch { /* state unreadable — use model name as fallback */ }
    return null;
  };

  /** Read current generation number from CURRENT file (matches state.py). */
  const _currentGen = () => {
    try {
      const genPath = path.join(home, 'CURRENT');
      return parseInt(readFileSync(genPath, 'utf8').trim(), 10);
    } catch { return 0; }
  };

  const prepareLineReply = async (params) => {
    const { target } = params;
    let content = params.content;

    // Validate target
    if (!target || typeof target !== 'object') {
      return errorContent('INVALID_TARGET', 'target must be an object with chat_ref, chat_name, chat_type');
    }
    const { chat_ref, chat_type } = target;
    let { chat_name } = target;
    if (!chat_ref || !CHAT_REF_RE.test(chat_ref)) {
      return errorContent('INVALID_CHAT_REF', 'target.chat_ref must match chat:<24hex>');
    }

    // F7: resolve display name from known_chats instead of trusting model
    const resolvedName = resolveKnownChatName(chat_ref);
    if (resolvedName) {
      chat_name = resolvedName;
    } else if (!chat_name || typeof chat_name !== 'string') {
      return errorContent('UNKNOWN_CHAT_REF', `chat_ref ${chat_ref} not found in known_chats and no fallback name`);
    }

    if (chat_name.length > 200) {
      return errorContent('INVALID_CHAT_NAME', 'chat_name exceeds 200 characters');
    }
    if (!['direct', 'group'].includes(chat_type)) {
      return errorContent('INVALID_CHAT_TYPE', 'target.chat_type must be "direct" or "group"');
    }

    // Validate content
    if (typeof content !== 'string' || content.length === 0) {
      return errorContent('INVALID_CONTENT', 'content must be a non-empty string');
    }
    if (content.length > MAX_TEXT_LENGTH) {
      return errorContent('CONTENT_TOO_LONG', `content exceeds ${MAX_TEXT_LENGTH} characters`);
    }
    // Sanitize control characters (keep \n, \t)
    content = sanitizeContent(content);
    if (content.length === 0) {
      return errorContent('INVALID_CONTENT', 'content is empty after sanitization');
    }

    const id = newActionId();
    const nonce = newNonce();
    const payload = {
      kind: 'line_reply',
      target: { chat_ref, chat_name, chat_type },
      content,
    };
    const { target_display, content_display } = deriveDisplay(payload);

    const record = {
      id,
      kind: 'line_reply',
      target: { chat_ref, chat_name, chat_type },
      content,
      payload_sha256: sha256(payload),
      content_sha256: sha256Raw(content),
      target_id: chat_ref,
      nonce,
      created_at: now().toISOString(),
      expires_at: new Date(now().getTime() + EXPIRY_MS).toISOString(),
    };

    await ensureDirs(home);
    await writePending(home, id, record);

    return {
      content: [{
        type: 'text',
        text: JSON.stringify({
          id,
          target_display,
          content_display,
          expires_at: record.expires_at,
          note: '請將 target_display 和 content_display 原封不動帶給 commit_action。',
        }),
      }],
    };
  };

  const prepareCalendarEvent = async (target, allowDuplicate) => {
    if (!target || typeof target !== 'object') {
      return errorContent('INVALID_TARGET', 'target must be an object');
    }
    const { start_iso, end_iso, subject, location, attendees } = target;
    if (!start_iso || !end_iso || !subject) {
      return errorContent('INVALID_TARGET', 'target must include start_iso, end_iso, subject');
    }
    if (typeof subject !== 'string' || subject.length === 0 || subject.length > 500) {
      return errorContent('INVALID_SUBJECT', 'subject must be 1-500 chars');
    }

    // Q-1: Check for similar events (unless allow_duplicate)
    if (!allowDuplicate && calendarAdapter) {
      try {
        const existing = await calendarAdapter.findSimilar({
          startIso: start_iso, endIso: end_iso, subject,
        });
        const similar = findSimilarEvents(
          { start_iso, end_iso, subject },
          existing,
        );
        if (similar.length > 0) {
          return {
            content: [{
              type: 'text',
              text: JSON.stringify({
                duplicate_candidates: similar,
                note: '發現同日相似事件。如確定要建立，請加 allow_duplicate: true 重新 prepare。',
              }),
            }],
          };
        }
      } catch (err) {
        stderr(`findSimilar error: ${err.code || err.message}`);
        // Non-fatal: proceed without duplicate check
      }
    }

    const id = newActionId();
    const transactionId = newNonce();
    const attendeeList = Array.isArray(attendees) ? attendees : (attendees ? [attendees] : []);
    const payload = {
      kind: 'calendar_event',
      target: {
        start_iso, end_iso, subject,
        ...(location ? { location } : {}),
        ...(attendeeList.length > 0 ? { attendees: attendeeList } : {}),
      },
    };
    const { target_display, content_display } = deriveDisplay(payload);

    const record = {
      id,
      kind: 'calendar_event',
      target: payload.target,
      payload_sha256: sha256(payload),
      content_sha256: sha256Raw(subject),
      target_id: `calendar:${start_iso}`,
      nonce: transactionId,
      created_at: now().toISOString(),
      expires_at: new Date(now().getTime() + EXPIRY_MS).toISOString(),
    };

    await ensureDirs(home);
    await writePending(home, id, record);

    return {
      content: [{
        type: 'text',
        text: JSON.stringify({
          id,
          target_display,
          content_display,
          expires_at: record.expires_at,
          note: '請將 target_display 和 content_display 原封不動帶給 commit_action。',
        }),
      }],
    };
  };

  // ── commit_action ──

  const handleCommit = async (params) => {
    const { id, target_display, content_display } = params;

    // Validate ID format (path traversal prevention)
    if (!isValidId(id)) {
      return errorContent('INVALID_ID', 'id must be 32 lowercase hex characters');
    }

    await ensureDirs(home);

    // Atomic ownership claim
    const claimed = await claimOwnership(home, id);
    if (!claimed) {
      // Check if already processed
      const done = await readDone(home, id);
      if (done) {
        return { content: [{ type: 'text', text: JSON.stringify({ status: 'already_processed', id, original_status: done.status }) }] };
      }
      return errorContent('NOT_FOUND', 'Action not found or already claimed');
    }

    // Read committing record
    const record = await readCommitting(home, id);
    if (!record) {
      return errorContent('INTERNAL_ERROR', 'Failed to read committing record');
    }

    // Check expiry
    const expiresAt = new Date(record.expires_at).getTime();
    if (now().getTime() > expiresAt) {
      await rejectAndFinalize(home, id, record, 'EXPIRED', 'Action expired');
      return errorContent('EXPIRED', 'Action has expired (10 minute limit)');
    }

    // Verify payload hash integrity
    let expectedPayload;
    if (record.kind === 'line_reply') {
      expectedPayload = { kind: 'line_reply', target: record.target, content: record.content };
    } else {
      expectedPayload = { kind: 'calendar_event', target: record.target };
    }
    const computedHash = sha256(expectedPayload);
    if (computedHash !== record.payload_sha256) {
      await rejectAndFinalize(home, id, record, 'HASH_MISMATCH', 'Payload integrity check failed');
      return errorContent('HASH_MISMATCH', 'Payload integrity check failed');
    }

    // Verify display fields match exactly
    const derived = deriveDisplay(expectedPayload);
    if (target_display !== derived.target_display) {
      await rejectAndFinalize(home, id, record, 'TARGET_MISMATCH', 'target_display does not match');
      return errorContent('TARGET_MISMATCH', 'target_display does not match the prepared action');
    }
    if (content_display !== derived.content_display) {
      await rejectAndFinalize(home, id, record, 'CONTENT_MISMATCH', 'content_display does not match');
      return errorContent('CONTENT_MISMATCH', 'content_display does not match the prepared action');
    }

    // Execute via adapter
    let sendResult;
    let status = 'sent';
    try {
      if (record.kind === 'line_reply') {
        if (!lineAdapter) {
          await rejectAndFinalize(home, id, record, 'NO_ADAPTER', 'LINE adapter not available');
          return errorContent('NO_ADAPTER', 'LINE adapter is not configured');
        }
        sendResult = await lineAdapter.send({
          chatRef: record.target.chat_ref,
          chatName: record.target.chat_name,
          chatType: record.target.chat_type,
          text: record.content,
          nonce: record.nonce,
        });
      } else if (record.kind === 'calendar_event') {
        if (!calendarAdapter) {
          await rejectAndFinalize(home, id, record, 'NO_ADAPTER', 'Calendar adapter not available');
          return errorContent('NO_ADAPTER', 'Calendar adapter is not configured');
        }
        sendResult = await calendarAdapter.create({
          subject: record.target.subject,
          startIso: record.target.start_iso,
          endIso: record.target.end_iso,
          location: record.target.location || '',
          attendees: record.target.attendees || [],
          transactionId: record.nonce,
        });
      }
    } catch (err) {
      const errCode = err?.code || '';
      // Classify: uncertain if dispatch may have happened
      if (errCode === 'LINE_SEND_UNCERTAIN' || errCode === 'TIMEOUT_AFTER_DISPATCH') {
        status = 'uncertain';
        sendResult = { error: errCode };
      } else {
        status = 'failed';
        sendResult = { error: errCode || err.message };
      }
    }

    // Write done record (WITHOUT original content text).
    // Whitelist receipt fields to prevent leaking original content (upstream
    // send_message_auto fallback path includes args.message in return value).
    const safeReceipt = sendResult ? sanitizeReceipt(record.kind, sendResult) : null;
    const doneRecord = {
      id,
      status,
      kind: record.kind,
      target_id: record.target_id,
      content_sha256: record.content_sha256,
      nonce: record.nonce,
      created_at: record.created_at,
      committed_at: now().toISOString(),
      receipt: safeReceipt,
    };
    // Ensure chat info for verify (but NOT message text)
    if (record.kind === 'line_reply') {
      doneRecord.chat_ref = record.target.chat_ref;
      doneRecord.chat_name = record.target.chat_name;
      doneRecord.chat_type = record.target.chat_type;
    } else {
      doneRecord.subject_sha256 = sha256Raw(record.target.subject);
      doneRecord.start_iso = record.target.start_iso;
      doneRecord.end_iso = record.target.end_iso;
      doneRecord.transaction_id = record.nonce;
    }

    await finalize(home, id, doneRecord);

    return {
      content: [{
        type: 'text',
        text: JSON.stringify({ id, status, committed_at: doneRecord.committed_at }),
      }],
    };
  };

  // ── action_status ──

  const handleStatus = async (params) => {
    const { id } = params;
    if (!isValidId(id)) {
      return errorContent('INVALID_ID', 'id must be 32 lowercase hex characters');
    }

    await ensureDirs(home);

    // Check done first
    const done = await readDone(home, id);
    if (done) {
      // If uncertain, try to verify
      if (done.status === 'uncertain') {
        const verifyResult = await tryVerify(done);
        if (verifyResult) {
          done.status = verifyResult.status;
          done.verified_at = now().toISOString();
          // Update done record
          const dirs = actionDirs(home);
          await atomicWriteJson(path.join(dirs.done, `${id}.json`), done);
        }
      }
      return { content: [{ type: 'text', text: JSON.stringify({ id, status: done.status, kind: done.kind }) }] };
    }

    // Check committing (should not happen normally after startup recovery)
    const committing = await readCommitting(home, id);
    if (committing) {
      return { content: [{ type: 'text', text: JSON.stringify({ id, status: 'committing', kind: committing.kind }) }] };
    }

    // Check pending
    const pending = await readPending(home, id);
    if (pending) {
      const expired = now().getTime() > new Date(pending.expires_at).getTime();
      return {
        content: [{
          type: 'text',
          text: JSON.stringify({ id, status: expired ? 'expired' : 'pending', kind: pending.kind }),
        }],
      };
    }

    return errorContent('NOT_FOUND', `No action found with id ${id}`);
  };

  /** Try to verify an uncertain action via adapter. Never resends. */
  const tryVerify = async (done) => {
    try {
      if (done.kind === 'line_reply' && lineAdapter) {
        const result = await lineAdapter.verify({
          chatRef: done.chat_ref,
          chatName: done.chat_name,
          chatType: done.chat_type,
          contentSha256: done.content_sha256,
          committedAt: done.committed_at,
        });
        return { status: result.found ? 'sent' : 'failed' };
      }
      if (done.kind === 'calendar_event' && calendarAdapter) {
        const result = await calendarAdapter.verify({
          transactionId: done.transaction_id || done.nonce,
          subjectSha256: done.subject_sha256,
          startIso: done.start_iso,
          endIso: done.end_iso,
        });
        return { status: result.found ? 'sent' : 'failed' };
      }
    } catch (err) {
      stderr(`verify error: ${err.code || err.message}`);
    }
    return null; // Could not verify, remain uncertain
  };

  // ── Helpers ──

  /** Whitelist receipt fields to prevent leaking original content. */
  const sanitizeReceipt = (kind, result) => {
    if (result.error) return { error: result.error };
    if (kind === 'line_reply') {
      // Only safe fields from send_message_auto's RECORDED_LOCAL return
      return {
        status: result.status,
        operationId: result.operationId,
        sourceRef: result.sourceRef,
        sourceMessageId: result.sourceMessageId,
        sourceTimestamp: result.sourceTimestamp,
        reused: result.reused,
      };
    }
    if (kind === 'calendar_event') {
      return { id: result.id, status: result.status };
    }
    return {};
  };

  const errorContent = (code, message) => ({
    isError: true,
    content: [{ type: 'text', text: JSON.stringify({ error: code, message }) }],
  });

  const rejectAndFinalize = async (homeDir, id, record, code, reason) => {
    const doneRecord = {
      id,
      status: 'rejected',
      reason_code: code,
      reason,
      kind: record.kind,
      target_id: record.target_id,
      content_sha256: record.content_sha256,
      nonce: record.nonce,
      created_at: record.created_at,
      rejected_at: now().toISOString(),
    };
    await finalize(homeDir, id, doneRecord);
  };

  // ── MCP protocol handler ──

  const handleMessage = async (msg) => {
    // Notifications (no id): acknowledge silently
    if (!Object.hasOwn(msg, 'id')) return null;

    const { id, method, params } = msg;

    if (method === 'initialize') {
      initialized = true;
      return successResponse(id, {
        protocolVersion: params?.protocolVersion || '2024-11-05',
        capabilities: { tools: {} },
        serverInfo: { name: 'daily-agenda-actions', version: '0.2.0' },
      });
    }

    if (method === 'ping') {
      return successResponse(id, {});
    }

    if (method === 'tools/list') {
      return successResponse(id, { tools: TOOLS });
    }

    if (method === 'tools/call') {
      const toolName = params?.name;
      const toolParams = params?.arguments || {};
      try {
        let result;
        if (toolName === 'prepare_action') {
          result = await handlePrepare(toolParams);
        } else if (toolName === 'commit_action') {
          result = await handleCommit(toolParams);
        } else if (toolName === 'action_status') {
          result = await handleStatus(toolParams);
        } else {
          return errorResponse(id, METHOD_NOT_FOUND, `Unknown tool: ${toolName}`);
        }
        return successResponse(id, result);
      } catch (err) {
        stderr(`tool error ${toolName}: ${err.message}`);
        return successResponse(id, errorContent('INTERNAL_ERROR', err.message));
      }
    }

    return errorResponse(id, METHOD_NOT_FOUND, `Unknown method: ${method}`);
  };

  return { handleMessage, home };
};

// ── Main entry point ───────────────────────────────────────────

const main = async () => {
  // Adapter selection: only 'fake' is allowed as test adapter
  const testAdapter = process.env.DAILY_AGENDA_ACTIONS_TEST_ADAPTER;
  let lineAdapter = null;
  let calendarAdapter = null;

  if (testAdapter !== undefined) {
    if (testAdapter !== 'fake') {
      process.stderr.write(`FATAL: DAILY_AGENDA_ACTIONS_TEST_ADAPTER must be 'fake' or unset, got '${testAdapter}'\n`);
      process.exit(1);
    }
    lineAdapter = createFakeLineAdapter();
    calendarAdapter = createFakeCalendarAdapter();
  }
  // Real adapters would be initialized here from config; not implemented yet (UNVERIFIED blocked by 0.3)

  const home = getHome();
  await ensureDirs(home);
  const recovered = await recoverCommitting(home);
  if (recovered.length > 0) {
    process.stderr.write(`recovered ${recovered.length} uncertain actions: ${recovered.join(', ')}\n`);
  }

  const server = createServer({ home, lineAdapter, calendarAdapter });
  const write = (line) => {
    process.stdout.write(line + '\n');
  };

  createLineReader(process.stdin, async (line) => {
    const msg = parseMessage(line);
    if (!msg) {
      write(errorResponse(null, PARSE_ERROR, 'Parse error'));
      return;
    }
    const response = await server.handleMessage(msg);
    if (response !== null) write(response);
  });
};

// Only run main when executed directly (exact path comparison, not substring)
import { fileURLToPath } from 'node:url';
const __server_file = fileURLToPath(import.meta.url);
const isDirectExecution = (() => {
  try {
    if (!process.argv[1]) return false;
    const a = path.resolve(process.argv[1]);
    const b = path.resolve(__server_file);
    // On Windows, paths may differ in case/slashes; normalize both
    return process.platform === 'win32'
      ? a.toLowerCase() === b.toLowerCase()
      : a === b;
  } catch {
    return false;
  }
})();

if (isDirectExecution) {
  main().catch((err) => {
    process.stderr.write(`FATAL: ${err.message}\n`);
    process.exit(1);
  });
}
