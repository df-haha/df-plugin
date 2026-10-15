import { createRequire } from 'node:module';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

// Resolve dependencies from this published package, never from a caller's
// working directory or another local checkout.
const PACKAGE_JSON = fileURLToPath(new URL('../../package.json', import.meta.url));
export const PACKAGE_ROOT = path.dirname(PACKAGE_JSON);
const packageRequire = createRequire(PACKAGE_JSON);

export function runtimeRequire() {
  return packageRequire;
}

export async function runtimeImport(specifier) {
  return import(pathToFileURL(runtimeRequire().resolve(specifier)).href);
}

/**
 * The optional Windows CUA backend must be explicitly configured. This only
 * parses configuration; filesystem validation happens when a UI operation is
 * requested, so importing the extension cannot disable other LINE features.
 */
export function configuredCuaDriverPath(value = process.env.LINE_MCP_CUA_DRIVER) {
  if (typeof value !== 'string' || !value || value !== value.trim() || value.includes('\0')) return null;
  if (!path.isAbsolute(value) || path.extname(value).toLowerCase() !== '.exe') return null;
  return value;
}

/** Optional local reader: an explicit executable, never an implicit PATH lookup. */
export function configuredPythonPath(value = process.env.LINE_MCP_PYTHON) {
  if (typeof value !== 'string' || !value || value !== value.trim() || value.includes('\0')) return null;
  if (!path.isAbsolute(value) || path.extname(value).toLowerCase() !== '.exe') return null;
  return value;
}

export class LineToolError extends Error {
  constructor(code, message, details = {}) {
    super(message);
    this.name = 'LineToolError';
    this.code = code;
    this.details = details;
    this.operationMayHaveCompleted = details.operationMayHaveCompleted === true;
  }
}

export function requireText(value, name, max = 10000, { empty = false } = {}) {
  if (typeof value !== 'string' || (!empty && !value.trim()) || value.length > max || value.includes('\0')) {
    throw new LineToolError('LINE_INVALID_ARGUMENT', `${name} must be ${empty ? 'a' : 'a nonempty'} string of at most ${max} characters without NUL.`);
  }
  return value;
}

export function requireChat(value) {
  requireText(value, 'chatName', 200);
  if (/[\r\n\t]/.test(value) || value !== value.trim()) {
    throw new LineToolError('LINE_INVALID_ARGUMENT', 'chatName must be an exact single-line chat name without surrounding whitespace.');
  }
  return value;
}

export function requireInteger(value, name, min, max) {
  if (!Number.isInteger(value) || value < min || value > max) {
    throw new LineToolError('LINE_INVALID_ARGUMENT', `${name} must be an integer from ${min} to ${max}.`);
  }
  return value;
}

export function requireChoice(value, name, choices) {
  if (!choices.includes(value)) throw new LineToolError('LINE_INVALID_ARGUMENT', `${name} must be one of: ${choices.join(', ')}.`);
  return value;
}

export function toolResult(value, images = []) {
  return { content: [{ type: 'text', text: JSON.stringify(value) }, ...images] };
}

export function toolError(error) {
  return { isError: true, ...toolResult({
    success: false,
    code: typeof error?.code === 'string' ? error.code : 'LINE_OPERATION_FAILED',
    message: error?.message || 'Unknown LINE operation failure.',
    operationMayHaveCompleted: error?.operationMayHaveCompleted === true,
    ...(error?.details || {}),
  }) };
}
