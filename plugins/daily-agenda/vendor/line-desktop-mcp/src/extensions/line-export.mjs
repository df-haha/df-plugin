import fs from 'node:fs/promises';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { LineToolError, requireChoice, requireText } from './line-runtime.mjs';
import { formatLineHistory } from './line-history.mjs';
import { readLocalLineMessages, validateLocalScope } from './line-local-reader.mjs';

/** Shared by the GUI MCP export and the local-only CLI export. */
export async function validateExportPath(outputPath, format, { fileSystem = fs } = {}) {
  requireText(outputPath, 'outputPath', 4096);
  if (!path.isAbsolute(outputPath) || /^\\\\/.test(outputPath) || path.extname(outputPath).toLowerCase() !== `.${format}`) throw new LineToolError('LINE_INVALID_ARGUMENT', 'Export requires an absolute local path whose extension matches txt/json/csv.');
  const normalizedPath = path.resolve(outputPath);
  if (process.platform === 'win32') {
    if (!/^[A-Za-z]:[\\/]/.test(outputPath)) throw new LineToolError('LINE_INVALID_ARGUMENT', 'Export requires a drive-qualified local Windows path.');
    if (outputPath.slice(2).includes(':')) throw new LineToolError('LINE_INVALID_ARGUMENT', 'Export path cannot contain Windows alternate data stream syntax.');
  }
  const parsed = path.parse(normalizedPath);
  let current = parsed.root;
  for (const component of normalizedPath.slice(parsed.root.length).split(path.sep).slice(0, -1)) {
    current = path.join(current, component);
    const stat = await fileSystem.lstat(current);
    if (!stat.isDirectory() || stat.isSymbolicLink()) throw new LineToolError('LINE_EXPORT_PATH_UNSAFE', 'Export parent must be an existing regular directory without junctions or symlinks.');
  }
  try { await fileSystem.lstat(outputPath); }
  catch (error) { if (error.code === 'ENOENT') return; throw error; }
  throw new LineToolError('LINE_EXPORT_EXISTS', 'Export refuses to overwrite an existing path. Choose a new filename.');
}

export async function writeVerifiedExport(outputPath, content, { fileSystem = fs } = {}) {
  const handle = await fileSystem.open(outputPath, 'wx');
  let postCreateFailed = false;
  try {
    await handle.writeFile(content, 'utf8');
    await handle.sync();
  } catch { postCreateFailed = true; }
  try { await handle.close(); }
  catch { postCreateFailed = true; }
  if (postCreateFailed) throw exportUnverifiedError();
  let bytes;
  try {
    bytes = await fileSystem.readFile(outputPath);
    if (!Buffer.isBuffer(bytes)) throw new TypeError('Export readback was not a byte buffer.');
  } catch { throw exportUnverifiedError(); }
  if (!bytes.equals(Buffer.from(content))) {
    throw new LineToolError('LINE_EXPORT_VERIFY_FAILED', 'Export readback differs. The created file was preserved for inspection.',
      { operationMayHaveCompleted: true, outputPathCreated: true });
  }
  return bytes;
}

function exportUnverifiedError() {
  return new LineToolError('LINE_EXPORT_UNVERIFIED',
    'Export file was created but could not be durably written and read back. The created file was preserved for inspection.',
    { operationMayHaveCompleted: true, outputPathCreated: true });
}

/** One bounded metadata page. Does not call GUI history or expand pagination. */
export async function exportLocalLineMessages(args, { localReader = readLocalLineMessages, fileSystem = fs } = {}) {
  if (!args || typeof args !== 'object' || Array.isArray(args)) throw new LineToolError('LINE_INVALID_ARGUMENT', 'Export requires a scoped request.');
  const { outputPath, format, ...requestedScope } = args;
  requireChoice(format, 'format', ['json', 'txt', 'csv']);
  const scope = validateLocalScope(requestedScope);
  if (scope.mediaMode !== 'metadata' || scope.mediaSourceRefs !== undefined) {
    throw new LineToolError('LINE_INVALID_ARGUMENT', 'Local export supports metadata only.');
  }
  await validateExportPath(outputPath, format, { fileSystem });
  const data = await localReader(scope);
  const exportFormat = 'line-local-history-v1';
  const columns = ['date', 'time', 'sender', 'kind', 'text'];
  const omittedFields = format === 'json' ? []
    : [...new Set(data.messages.flatMap(message => Object.keys(message)))].filter(key => !columns.includes(key)).sort();
  const content = format === 'json'
    ? JSON.stringify({ ...data, exportFormat }, null, 2) + '\n'
    : formatLineHistory(data.messages, { format }) + '\n';
  const bytes = await writeVerifiedExport(outputPath, content, { fileSystem });
  return {
    chatName: data.chatName, outputPath, format, exportFormat,
    bytes: bytes.length, sha256: createHash('sha256').update(bytes).digest('hex'),
    count: data.count, scope: data.scope, freshness: data.freshness,
    pagination: data.pagination, warnings: data.warnings ?? [], verified: true,
    ...(format === 'json' ? {} : { projection: {
      columns, omittedFields,
      note: 'TXT/CSV omit every field outside these five columns; omittedFields lists those present in this page. Missing values, including kind when the reader does not provide it, are blank; contentType is not inferred as kind. Scope, freshness and pagination are in this result; use JSON for the complete page.',
    } }),
  };
}
