const SAFE_ERROR_CODE = /^[A-Z][A-Z0-9_]{1,127}$/u;

export const CLI_EXIT_CODES = Object.freeze({
  success: 0,
  internal: 1,
  input: 2,
  scope: 3,
  runtime: 4,
  execution: 5,
  sideEffectUncertain: 6,
});

const INPUT_CODES = new Set([
  'CLI_INVALID_ARGUMENT',
  'CLI_MISSING_ARGUMENT',
  'CLI_UNKNOWN_COMMAND',
  'CLI_UNSUPPORTED_OPTION',
  'EEXIST',
  'EINVAL',
  'ENOENT',
  'ENOTDIR',
  'LINE_INVALID_ARGUMENT',
  'LINE_EXPORT_EXISTS',
  'LINE_EXPORT_PATH_UNSAFE',
]);

const SCOPE_CODES = new Set([
  'CHAT_AMBIGUOUS',
  'CHAT_NOT_FOUND',
  'CURSOR_SCOPE_MISMATCH',
  'INVALID_CURSOR',
  'LOCAL_READER_SCOPE_MISMATCH',
]);

const RUNTIME_CODES = new Set([
  'ENGINE_CIPHER_UNAVAILABLE',
  'ENGINE_DLL_INVALID_PATH',
  'ENGINE_DLL_UNAVAILABLE',
  'ENGINE_DLL_UNCONFIGURED',
  'ENGINE_INTEGRITY_FAILED',
  'LINE_BUILD_UNVERIFIED',
  'LINE_CLIENT_STATUS_UNAVAILABLE',
  'LINE_PROCESS_AMBIGUOUS',
  'LINE_PROCESS_UNAVAILABLE',
  'LOCAL_READER_UNAVAILABLE',
  'LOCAL_READER_TIMEOUT_INVALID',
  'RUNTIME_DIRECTORY_UNAVAILABLE',
  'SESSION_KEY_UNAVAILABLE',
]);

const EXECUTION_CODES = new Set([
  'DATABASE_HEADER_INVALID',
  'DATABASE_READ_FAILED',
  'DATABASE_SIZE_INVALID',
  'EACCES',
  'EBUSY',
  'EIO',
  'EMFILE',
  'ENFILE',
  'ENOSPC',
  'EPERM',
  'EROFS',
  'INVALID_PATH',
  'INVALID_SOURCE_ID',
  'INVALID_SOURCE_TIME',
  'LOCAL_READER_CLEANUP_FAILED',
  'LOCAL_READER_FAILED',
  'LOCAL_READER_INVALID_RESULT',
  'LOCAL_READER_RESULT_TOO_LARGE',
  'LOCAL_READER_TERMINATION_FAILED',
  'LOCAL_READER_TIMEOUT',
  'MAIN_DATABASE_AMBIGUOUS',
  'RESULT_TOO_LARGE',
  'SESSION_KEY_CHANGED',
  'SNAPSHOT_CLEANUP_FAILED',
  'SNAPSHOT_DESTINATION_EXISTS',
  'SNAPSHOT_DISK_FULL',
  'SNAPSHOT_IO_ERROR',
  'SOURCE_ACCESS_DENIED',
  'SOURCE_BUSY',
  'SOURCE_IO_ERROR',
  'SOURCE_LIMIT_INVALID',
  'SOURCE_NOT_FILE',
  'SOURCE_NOT_FOUND',
  'SOURCE_NOT_READONLY',
  'SOURCE_REPARSE',
  'SOURCE_TOO_LARGE',
  'WAL_HEADER_INVALID',
  'WAL_NO_VALID_COMMIT',
  'WAL_PAGE_SIZE_MISMATCH',
]);

const SAFE_DETAIL_FIELDS = Object.freeze({
  mayStillBeRunning: value => value === true,
  maxBytes: value => Number.isSafeInteger(value) && value >= 0,
  operationMayHaveCompleted: value => value === true,
  outputPathCreated: value => value === true,
  setting: value => typeof value === 'string' && /^[A-Z][A-Z0-9_]{1,127}$/u.test(value),
  sourceBytes: value => Number.isSafeInteger(value) && value >= 0,
  sourceKind: value => typeof value === 'string' && /^[a-z][a-z_]{0,63}$/u.test(value),
});

const SAFE_MESSAGES = Object.freeze({
  input: 'The command input was rejected.',
  scope: 'The requested chat or pagination scope was rejected.',
  runtime: 'A required local LINE runtime or verified client build is unavailable.',
  execution: 'The local LINE operation did not complete.',
  uncertain: 'The operation may have created an export. Inspect it before retrying.',
  internal: 'The CLI could not complete the requested operation.',
});

/** A CLI-owned, intentionally non-sensitive error. */
export class CliError extends Error {
  constructor(code, message, { exitCode, details = {}, operationMayHaveCompleted = false } = {}) {
    super(message);
    this.name = 'CliError';
    this.code = code;
    this.exitCode = exitCode;
    this.details = details;
    this.operationMayHaveCompleted = operationMayHaveCompleted === true;
  }
}

export function safeErrorCode(error) {
  return typeof error?.code === 'string' && SAFE_ERROR_CODE.test(error.code)
    ? error.code : 'LINE_CLI_OPERATION_FAILED';
}

export function operationMayHaveCompleted(error) {
  return error?.operationMayHaveCompleted === true || error?.details?.operationMayHaveCompleted === true;
}

export function exitCodeForError(error) {
  if (operationMayHaveCompleted(error)) return CLI_EXIT_CODES.sideEffectUncertain;
  if (Number.isInteger(error?.exitCode) && Object.values(CLI_EXIT_CODES).includes(error.exitCode)) return error.exitCode;
  const code = safeErrorCode(error);
  if (INPUT_CODES.has(code)) return CLI_EXIT_CODES.input;
  if (SCOPE_CODES.has(code)) return CLI_EXIT_CODES.scope;
  if (RUNTIME_CODES.has(code)) return CLI_EXIT_CODES.runtime;
  if (EXECUTION_CODES.has(code)) return CLI_EXIT_CODES.execution;
  return CLI_EXIT_CODES.internal;
}

function safeDetails(error) {
  const source = error?.details;
  if (!source || typeof source !== 'object' || Array.isArray(source)) return undefined;
  const details = {};
  for (const [key, validate] of Object.entries(SAFE_DETAIL_FIELDS)) {
    if (validate(source[key])) details[key] = source[key];
  }
  return Object.keys(details).length > 0 ? details : undefined;
}

function safeMessage(error, exitCode) {
  if (error instanceof CliError) return error.message;
  if (exitCode === CLI_EXIT_CODES.sideEffectUncertain) return SAFE_MESSAGES.uncertain;
  if (exitCode === CLI_EXIT_CODES.input) return SAFE_MESSAGES.input;
  if (exitCode === CLI_EXIT_CODES.scope) return SAFE_MESSAGES.scope;
  if (exitCode === CLI_EXIT_CODES.runtime) return SAFE_MESSAGES.runtime;
  if (exitCode === CLI_EXIT_CODES.execution) return SAFE_MESSAGES.execution;
  return SAFE_MESSAGES.internal;
}

export function errorResult(error) {
  const exitCode = exitCodeForError(error);
  const details = safeDetails(error);
  const mayHaveCompleted = operationMayHaveCompleted(error);
  return {
    code: safeErrorCode(error),
    message: safeMessage(error, exitCode),
    ...(details ? { details } : {}),
    operationMayHaveCompleted: mayHaveCompleted,
    exitCode,
  };
}

export function successEnvelope({ requestId, command, data, packageVersion, source, durationMs }) {
  return {
    schemaVersion: 1,
    requestId,
    command,
    ok: true,
    data,
    meta: { packageVersion, source, durationMs },
  };
}

export function failureEnvelope({ requestId, command, error, packageVersion, source, durationMs }) {
  const result = errorResult(error);
  return {
    envelope: {
      schemaVersion: 1,
      requestId,
      command,
      ok: false,
      error: {
        code: result.code,
        message: result.message,
        ...(result.details ? { details: result.details } : {}),
        operationMayHaveCompleted: result.operationMayHaveCompleted,
      },
      meta: { packageVersion, source, durationMs },
    },
    exitCode: result.exitCode,
  };
}

export function formatHumanError(error) {
  const result = errorResult(error);
  const uncertainty = result.operationMayHaveCompleted ? ' operationMayHaveCompleted=true.' : '';
  return `error [${result.code}]: ${result.message}${uncertainty}\n`;
}

function terminalSafeText(text) {
  // Render untrusted controls visibly, including ESC/CSI/OSC, carriage return,
  // backspace and bidi formatting. Preserve ordinary multiline Unicode text.
  return text.replace(/[\x00-\x08\x0b-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]/gu,
    character => `\\u${character.charCodeAt(0).toString(16).padStart(4, '0')}`);
}

export function emitCliResult({ envelope, json, humanText, errorText, stdout, stderr }) {
  if (json) {
    stdout.write(`${JSON.stringify(envelope)}\n`);
    return;
  }
  if (errorText) stderr.write(terminalSafeText(errorText));
  else {
    const text = terminalSafeText(humanText);
    stdout.write(text.endsWith('\n') ? text : `${text}\n`);
  }
}
