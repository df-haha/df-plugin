import { randomUUID } from 'node:crypto';
import { createRequire } from 'node:module';
import { parseArgs } from 'node:util';

import { readLineClientStatus as defaultReadLineClientStatus } from '../extensions/line-client-status.mjs';
import { exportLocalLineMessages as defaultExportLocalLineMessages } from '../extensions/line-export.mjs';
import { readLocalLineMessages as defaultReadLocalLineMessages, validateLocalScope } from '../extensions/line-local-reader.mjs';
import {
  CLI_EXIT_CODES,
  CliError,
  emitCliResult,
  failureEnvelope,
  formatHumanError,
  successEnvelope,
} from './result.mjs';

const packageRequire = createRequire(import.meta.url);
export const PACKAGE_VERSION = packageRequire('../../package.json').version;

const OPTIONS = Object.freeze({
  help: { type: 'boolean' },
  version: { type: 'boolean' },
  json: { type: 'boolean' },
  chat: { type: 'string' },
  from: { type: 'string' },
  to: { type: 'string' },
  limit: { type: 'string' },
  'chat-type': { type: 'string' },
  query: { type: 'string' },
  cursor: { type: 'string' },
  format: { type: 'string' },
  out: { type: 'string' },
});

const COMMAND_OPTIONS = Object.freeze({
  help: new Set(['help', 'json']),
  version: new Set(['version', 'json']),
  status: new Set(['help', 'json']),
  capabilities: new Set(['help', 'json']),
  'messages.read': new Set(['help', 'json', 'chat', 'from', 'to', 'limit', 'chat-type', 'query', 'cursor']),
  'messages.export': new Set(['help', 'json', 'chat', 'from', 'to', 'limit', 'chat-type', 'query', 'cursor', 'format', 'out']),
});

export const CLI_CAPABILITIES = Object.freeze({
  commands: Object.freeze([
    Object.freeze({ id: 'status', availability: 'implemented', source: 'local-line-client-status',
      note: 'Build and process metadata only. It does not read chats or initialize GUI automation.' }),
    Object.freeze({ id: 'capabilities', availability: 'implemented', source: 'cli-static',
      note: 'Lists this M1 CLI surface without inspecting LINE or its runtime.' }),
    Object.freeze({ id: 'messages.read', availability: 'implemented', source: 'local-line-reader',
      note: 'Reads one exact chat and one bounded date range in metadata-only mode.' }),
    Object.freeze({ id: 'messages.export', availability: 'implemented', source: 'local-line-export',
      note: 'Exports one validated local-reader page to a new JSON, TXT, or CSV file.' }),
    Object.freeze({ id: 'mcp-gui-controls', availability: 'unavailable_cli', source: 'mcp-gui',
      note: 'M1 never constructs LineUi, initializes CUA, opens chats, stages drafts, or sends.' }),
    Object.freeze({ id: 'session', availability: 'deferred', source: 'cli-session',
      note: 'The interactive stdio session is deferred to M2.' }),
    Object.freeze({ id: 'send', availability: 'unavailable_cli', source: 'mcp-gui',
      note: 'Text sends, mentions, files, recall, and other GUI actions are unavailable from M1.' }),
  ]),
  limits: Object.freeze({
    maxDateRangeDays: 31,
    defaultMessageLimit: 200,
    maxMessageLimit: 1000,
    mediaMode: 'metadata',
    pagination: 'one page per command; preserve pagination.nextCursor and scope for the next request',
  }),
});

const HELP = Object.freeze({
  root: `Usage:
  line-cli --help
  line-cli --version
  line-cli status [--json]
  line-cli capabilities [--json]
  line-cli messages read --chat <exact-name> --from YYYY-MM-DD --to YYYY-MM-DD [--limit 1..1000] [--chat-type auto|group|direct] [--query <literal>] [--cursor <cursor>] [--json]
  line-cli messages export --chat <exact-name> --from YYYY-MM-DD --to YYYY-MM-DD --format json|txt|csv --out <absolute-new-path> [--limit 1..1000] [--chat-type auto|group|direct] [--query <literal>] [--cursor <cursor>] [--json]

M1 is local-read-only. It reads one exact chat and at most 31 days per command.
It does not start a GUI session, open a chat, stage a draft, or send a message.`,
  status: 'Usage: line-cli status [--json]\n\nReports verified LINE build and process metadata only. A valid not_running state is not a status-query failure.',
  capabilities: 'Usage: line-cli capabilities [--json]\n\nLists the M1 CLI capabilities and deferred GUI/session capabilities without inspecting LINE.',
  'messages.read': 'Usage: line-cli messages read --chat <exact-name> --from YYYY-MM-DD --to YYYY-MM-DD [--limit 1..1000] [--chat-type auto|group|direct] [--query <literal>] [--cursor <cursor>] [--json]\n\nUses the validated local reader in metadata-only mode. One command returns one page; preserve the returned cursor and identical scope when requesting another page.',
  'messages.export': 'Usage: line-cli messages export --chat <exact-name> --from YYYY-MM-DD --to YYYY-MM-DD --format json|txt|csv --out <absolute-new-path> [--limit 1..1000] [--chat-type auto|group|direct] [--query <literal>] [--cursor <cursor>] [--json]\n\nWrites only a new validated file. JSON preserves the reader record; TXT/CSV are projections. Existing files are never overwritten.',
});

const LOCAL_STATUS_STATES = new Set(['running', 'ambiguous', 'not_available', 'not_running', 'unverified']);

function cliError(code, message, exitCode = CLI_EXIT_CODES.input) {
  return new CliError(code, message, { exitCode });
}

function suppliedOptions(tokens) {
  return new Set(tokens.filter(token => token.kind === 'option').map(token => token.name));
}

function assertNoDuplicateOptions(tokens) {
  const seen = new Set();
  for (const token of tokens) {
    if (token.kind !== 'option') continue;
    if (seen.has(token.name)) {
      throw cliError('CLI_INVALID_ARGUMENT', `--${token.name} may be provided only once.`);
    }
    seen.add(token.name);
  }
}

function commandFromPositionals(positionals) {
  if (positionals.length === 1 && ['status', 'capabilities'].includes(positionals[0])) return positionals[0];
  if (positionals.length === 2 && positionals[0] === 'messages' && ['read', 'export'].includes(positionals[1])) {
    return `messages.${positionals[1]}`;
  }
  if (positionals.length === 0) throw cliError('CLI_MISSING_ARGUMENT', 'A command is required. Run line-cli --help.');
  throw cliError('CLI_UNKNOWN_COMMAND', 'The command is not supported. Run line-cli --help.');
}

function assertAllowedOptions(command, options) {
  const allowed = COMMAND_OPTIONS[command];
  for (const option of options) {
    if (!allowed?.has(option)) throw cliError('CLI_UNSUPPORTED_OPTION', `--${option} is not supported for ${command}.`);
  }
}

function requiredString(values, option) {
  const value = values[option];
  if (typeof value !== 'string' || value.length === 0) {
    throw cliError('CLI_MISSING_ARGUMENT', `--${option} is required for this command.`);
  }
  return value;
}

function parseLimit(value) {
  if (value === undefined) return 200;
  if (typeof value !== 'string' || !/^[1-9][0-9]*$/u.test(value)) {
    throw cliError('CLI_INVALID_ARGUMENT', '--limit must be an integer from 1 to 1000.');
  }
  const limit = Number(value);
  if (!Number.isSafeInteger(limit) || limit > 1000) {
    throw cliError('CLI_INVALID_ARGUMENT', '--limit must be an integer from 1 to 1000.');
  }
  return limit;
}

function readScope(values) {
  const scope = {
    chatName: requiredString(values, 'chat'),
    dateFrom: requiredString(values, 'from'),
    dateTo: requiredString(values, 'to'),
    messageLimit: parseLimit(values.limit),
  };
  if (values['chat-type'] !== undefined) scope.chatType = values['chat-type'];
  if (values.query !== undefined) scope.query = values.query;
  if (values.cursor !== undefined) scope.cursor = values.cursor;
  return scope;
}

function forCommand(command, action) {
  try {
    return action();
  } catch (error) {
    if (error && typeof error === 'object') error.cliCommand = command;
    throw error;
  }
}

/** Parse only CLI syntax and the M1 command allowlist. No LINE reader starts here. */
export function parseCliArguments(argv) {
  if (!Array.isArray(argv) || argv.some(value => typeof value !== 'string')) {
    throw cliError('CLI_INVALID_ARGUMENT', 'Arguments must be strings.');
  }
  let parsed;
  try {
    parsed = parseArgs({ args: argv, options: OPTIONS, allowPositionals: true, strict: true, tokens: true });
  } catch {
    throw cliError('CLI_INVALID_ARGUMENT', 'The command-line arguments are invalid. Run line-cli --help.');
  }
  const { values, positionals, tokens } = parsed;
  assertNoDuplicateOptions(tokens);
  const options = suppliedOptions(tokens);
  if (values.help === true && values.version === true) {
    throw cliError('CLI_INVALID_ARGUMENT', '--help and --version cannot be used together.');
  }
  if (values.version === true) {
    if (positionals.length !== 0 || [...options].some(option => !['version', 'json'].includes(option))) {
      throw cliError('CLI_UNSUPPORTED_OPTION', '--version cannot be combined with a command or other options.');
    }
    return { command: 'version', json: values.json === true };
  }
  if (values.help === true) {
    if (positionals.length === 0) {
      assertAllowedOptions('help', options);
      return { command: 'help', json: values.json === true, help: true };
    }
    const command = commandFromPositionals(positionals);
    assertAllowedOptions(command, options);
    return { command, json: values.json === true, help: true };
  }
  const command = commandFromPositionals(positionals);
  forCommand(command, () => assertAllowedOptions(command, options));
  if (command === 'messages.read') {
    return forCommand(command, () => ({ command, json: values.json === true, scope: readScope(values) }));
  }
  if (command === 'messages.export') {
    return forCommand(command, () => {
      const format = requiredString(values, 'format');
      if (!['json', 'txt', 'csv'].includes(format)) {
        throw cliError('CLI_INVALID_ARGUMENT', '--format must be json, txt, or csv.');
      }
      return { command, json: values.json === true, scope: readScope(values), outputPath: requiredString(values, 'out'), format };
    });
  }
  return { command, json: values.json === true };
}

function validStatus(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  return value.ok === true && value.client?.verified === true && LOCAL_STATUS_STATES.has(value.process?.state);
}

function buildUnverifiedStatus(value) {
  return value?.ok === false && value.code === 'LINE_BUILD_UNVERIFIED'
    && value.client?.verified === false && value.process?.state === 'not_checked';
}

function dependencies(overrides = {}) {
  const resolved = {
    readLineClientStatus: overrides.readLineClientStatus ?? defaultReadLineClientStatus,
    readLocalLineMessages: overrides.readLocalLineMessages ?? defaultReadLocalLineMessages,
    exportLocalLineMessages: overrides.exportLocalLineMessages ?? defaultExportLocalLineMessages,
  };
  for (const [name, value] of Object.entries(resolved)) {
    if (typeof value !== 'function') throw new CliError('LINE_CLI_OPERATION_FAILED', `${name} is not available.`, { exitCode: CLI_EXIT_CODES.internal });
  }
  return resolved;
}

function validateMetadataScope(scope) {
  // `validateLocalScope` enforces the inherited 31-day, exact-chat, cursor,
  // and page-size contract before the local reader or export helper can start.
  return validateLocalScope({ ...scope, mediaMode: 'metadata' });
}

export function sourceForCommand(command, { help = false } = {}) {
  if (help) return 'cli-static';
  if (command === 'status') return 'local-line-client-status';
  if (command === 'messages.read') return 'local-line-reader';
  if (command === 'messages.export') return 'local-line-export';
  return 'cli-static';
}

/** Execute a parsed command. Dependencies are explicit for fixture tests only. */
export async function executeCliCommand(parsed, overrides = {}) {
  if (!parsed || typeof parsed !== 'object') throw cliError('CLI_INVALID_ARGUMENT', 'A parsed command is required.');
  if (parsed.help) return { usage: HELP[parsed.command] ?? HELP.root };
  if (parsed.command === 'help') return { usage: HELP.root };
  if (parsed.command === 'version') return { version: PACKAGE_VERSION };
  if (parsed.command === 'capabilities') return CLI_CAPABILITIES;

  const implementation = dependencies(overrides);
  if (parsed.command === 'status') {
    const status = await implementation.readLineClientStatus();
    if (buildUnverifiedStatus(status)) {
      throw new CliError('LINE_BUILD_UNVERIFIED', 'The installed LINE client build is not verified for local status checks.', {
        exitCode: CLI_EXIT_CODES.runtime,
      });
    }
    if (!validStatus(status)) {
      throw new CliError('LINE_CLIENT_STATUS_UNAVAILABLE', 'LINE client status could not be queried.', {
        exitCode: CLI_EXIT_CODES.runtime,
      });
    }
    return status;
  }
  if (parsed.command === 'messages.read') {
    return implementation.readLocalLineMessages(validateMetadataScope(parsed.scope));
  }
  if (parsed.command === 'messages.export') {
    const scope = validateMetadataScope(parsed.scope);
    return implementation.exportLocalLineMessages({ outputPath: parsed.outputPath, format: parsed.format, ...scope });
  }
  throw cliError('CLI_UNKNOWN_COMMAND', 'The command is not supported. Run line-cli --help.');
}

function humanRead(data) {
  const requested = data.scope?.requested;
  const summary = [
    `chat: ${data.chatName ?? 'unknown'}`,
    `date: ${requested?.dateFrom ?? 'unknown'}..${requested?.dateTo ?? 'unknown'}`,
    `messages: ${data.count ?? 0}`,
    `hasMore: ${data.pagination?.hasMore === true ? 'true' : 'false'}`,
    `scope: ${JSON.stringify(data.scope ?? null)}`,
    `freshness: ${JSON.stringify(data.freshness ?? null)}`,
    `warnings: ${JSON.stringify(data.warnings ?? [])}`,
  ];
  if (data.pagination?.nextCursor) summary.push(`nextCursor: ${data.pagination.nextCursor}`);
  const messages = Array.isArray(data.messages) ? data.messages : [];
  for (const message of messages) {
    const identity = [message.date, message.time, message.sender, message.kind].filter(value => typeof value === 'string' && value.length > 0).join(' ');
    summary.push('', `[${message.sourceRef ?? 'message'}]${identity ? ` ${identity}` : ''}`);
    summary.push(typeof message.text === 'string' ? message.text : '[No text; inspect --json output for message metadata.]');
  }
  return summary.join('\n');
}

export function formatHumanSuccess(command, data) {
  if (command === 'help' || data?.usage) return data.usage;
  if (command === 'version') return data.version;
  if (command === 'status') {
    return `client: ${data.client?.verified === true ? 'verified' : 'unverified'}\nprocess: ${data.process?.state ?? 'unknown'}`;
  }
  if (command === 'capabilities') {
    return data.commands.map(item => `${item.id}: ${item.availability}`).join('\n');
  }
  if (command === 'messages.read') return humanRead(data);
  if (command === 'messages.export') {
    const requested = data.scope?.requested;
    const pagination = data.pagination ?? {};
    return [
      `exported: ${data.outputPath}`,
      `format: ${data.format}`,
      `messages: ${data.count}`,
      `date: ${requested?.dateFrom ?? 'unknown'}..${requested?.dateTo ?? 'unknown'}`,
      `hasMore: ${pagination.hasMore === true ? 'true' : 'false'}`,
      ...(pagination.nextCursor ? [`nextCursor: ${pagination.nextCursor}`] : []),
      `scope: ${JSON.stringify(data.scope ?? null)}`,
      `freshness: ${JSON.stringify(data.freshness ?? null)}`,
      `warnings: ${JSON.stringify(data.warnings ?? [])}`,
      ...(data.projection ? [`projection: ${JSON.stringify(data.projection)}`] : []),
      `sha256: ${data.sha256}`,
      `verified: ${data.verified === true ? 'true' : 'false'}`,
    ].join('\n');
  }
  return JSON.stringify(data, null, 2);
}

function jsonRequested(argv) {
  return Array.isArray(argv) && argv.includes('--json');
}

/** Run the executable contract with injectable readers and streams for tests. */
export async function runCli(argv, overrides = {}, {
  stdout = process.stdout,
  stderr = process.stderr,
  now = () => Date.now(),
  requestId = randomUUID,
  packageVersion = PACKAGE_VERSION,
} = {}) {
  const startedAt = now();
  const id = typeof requestId === 'function' ? requestId() : requestId;
  let parsed;
  let command = 'unknown';
  let json = jsonRequested(argv);
  try {
    parsed = parseCliArguments(argv);
    command = parsed.command;
    json = parsed.json;
    const data = await executeCliCommand(parsed, overrides);
    const envelope = successEnvelope({ requestId: id, command, data, packageVersion,
      source: sourceForCommand(command, parsed), durationMs: Math.max(0, now() - startedAt) });
    emitCliResult({ envelope, json, humanText: formatHumanSuccess(command, data), stdout, stderr });
    return CLI_EXIT_CODES.success;
  } catch (error) {
    if (typeof error?.cliCommand === 'string') command = error.cliCommand;
    const failure = failureEnvelope({ requestId: id, command, error, packageVersion,
      source: sourceForCommand(command), durationMs: Math.max(0, now() - startedAt) });
    emitCliResult({ envelope: failure.envelope, json, errorText: formatHumanError(error), stdout, stderr });
    return failure.exitCode;
  }
}
