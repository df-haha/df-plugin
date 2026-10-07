/**
 * line_collect.mjs — Bridge between daily-agenda's Python collector and the
 * vendored line-desktop-mcp local reader.
 *
 * Usage: node line_collect.mjs --vendor <dir>
 * stdin: one JSON request  {"op":"probe"|"recentChats"|"messages", ...}
 * stdout: one JSON response {ok, ...} or {ok:false, code}
 *
 * Only imports read-only functions from the vendor.  Never imports send or
 * extension modules.  LINE_MCP_EXTENSIONS is stripped from the environment
 * before any vendor code runs (Python side also strips it, but defence-in-depth).
 */

import { pathToFileURL } from 'node:url';
import path from 'node:path';

delete process.env.LINE_MCP_EXTENSIONS;

const args = process.argv.slice(2);
const vendorIdx = args.indexOf('--vendor');
if (vendorIdx === -1 || !args[vendorIdx + 1]) {
  process.stdout.write(JSON.stringify({ ok: false, code: 'LINE_INVALID_ARGUMENT' }));
  process.exit(0);
}
const vendorDir = path.resolve(args[vendorIdx + 1]);

let readerModule;
try {
  const readerPath = path.join(vendorDir, 'src', 'extensions', 'line-local-reader.mjs');
  readerModule = await import(pathToFileURL(readerPath).href);
} catch {
  process.stdout.write(JSON.stringify({ ok: false, code: 'LINE_RUNTIME_MISSING' }));
  process.exit(0);
}

const { readLocalLineRecentChats, readLocalLineMessages } = readerModule;

let input = '';
for await (const chunk of process.stdin) {
  input += chunk;
}

let request;
try {
  request = JSON.parse(input);
} catch {
  process.stdout.write(JSON.stringify({ ok: false, code: 'LINE_INVALID_ARGUMENT' }));
  process.exit(0);
}

const op = request?.op;

try {
  if (op === 'probe') {
    const start = Date.now();
    try {
      const result = await readLocalLineRecentChats({ days: 14, limit: 1 });
      process.stdout.write(JSON.stringify({
        ok: true, code: null, ms: Date.now() - start,
      }));
    } catch (err) {
      process.stdout.write(JSON.stringify({
        ok: false, code: err?.code ?? 'LOCAL_READER_FAILED', ms: Date.now() - start,
      }));
    }
  } else if (op === 'recentChats') {
    const scope = { days: request.days ?? 30, limit: request.limit ?? 50 };
    // offset only when > 0 — allows unpatched vendor to work for page 0
    if (request.offset > 0) {
      scope.offset = request.offset;
    }
    const result = await readLocalLineRecentChats(scope);
    process.stdout.write(JSON.stringify({
      ok: true,
      chats: result.chats,
      hasMore: result.hasMore,
      checkedAt: result.checkedAt,
      ownSenderRef: result.ownSenderRef,
      dateFrom: result.dateFrom,
      dateTo: result.dateTo,
    }));
  } else if (op === 'messages') {
    const scope = {
      chatName: request.chatName,
      chatType: request.chatType ?? 'auto',
      dateFrom: request.dateFrom,
      dateTo: request.dateTo,
      messageLimit: request.messageLimit ?? 200,
    };
    if (request.expectedChatRef) {
      scope.expectedChatRef = request.expectedChatRef;
    }
    if (request.cursor) {
      scope.cursor = request.cursor;
    }
    const result = await readLocalLineMessages(scope);
    process.stdout.write(JSON.stringify({
      ok: true,
      chatRef: result.chatRef,
      chatName: result.chatName,
      chatType: result.chatIdentity?.kind ?? null,
      ownSenderRef: result.ownSenderRef,
      messages: result.messages.map(m => ({
        sourceRef: m.sourceRef,
        sourceMessageId: m.sourceMessageId,
        sourceTimestamp: m.sourceTimestamp,
        date: m.date,
        time: m.time,
        text: m.text,
        senderRef: m.senderRef,
        sender: m.sender,
        contentType: m.contentType,
      })),
      count: result.count,
      hasMore: result.pagination?.hasMore ?? false,
      nextCursor: result.pagination?.nextCursor ?? null,
    }));
  } else {
    process.stdout.write(JSON.stringify({ ok: false, code: 'LINE_INVALID_ARGUMENT' }));
  }
} catch (err) {
  process.stdout.write(JSON.stringify({
    ok: false,
    code: err?.code ?? 'LOCAL_READER_FAILED',
  }));
}
