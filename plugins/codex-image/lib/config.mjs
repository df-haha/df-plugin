/**
 * config.mjs — Read/write codex-image.local.md config (YAML-ish frontmatter + markdown body).
 */
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';

/** Current schema version. */
export const CURRENT_SCHEMA_VERSION = 1;

/** Detected field names (auto-populated by setup/smoke). */
export const DETECTED_FIELDS = [
  'platform', 'is_wsl', 'codex_cli_version', 'codex_logged_in', 'image_generation_feature',
  'detected_dispatch_model', 'detected_dispatch_effort', 'python_cmd',
  'pillow_available', 'openai_api_key_present', 'network_access_configured',
  'smoke_status', 'last_smoke_at',
];

/** User preference field names. */
export const PREFERENCE_FIELDS = [
  'default_quality', 'default_size', 'default_output_dir', 'deny_write_paths',
  'allow_cli_fallback', 'timeout_seconds', 'max_parallel',
  'override_dispatch_model', 'quality_hint_mode',
];

/** Meta field names. */
export const META_FIELDS = [
  'schema_version', 'setup_version', 'setup_at',
];

/**
 * Normalize a tri-state value to 'true' | 'false' | 'unknown'.
 * @param {*} value
 * @returns {'true' | 'false' | 'unknown'}
 */
export function triState(value) {
  if (value === undefined || value === null) return 'unknown';
  if (value === true || value === 'true') return 'true';
  if (value === false || value === 'false') return 'false';
  return 'unknown';
}

const USER_NOTES_BEGIN = '<!-- codex-image:user-notes:begin -->';
const USER_NOTES_END = '<!-- codex-image:user-notes:end -->';

/**
 * Parse a config file's text into fields, body, and user notes.
 * @param {string} text
 * @returns {{ fields: Record<string,string>, body: string, userNotes: string|null, malformed?: boolean }}
 */
/**
 * Strip a trailing `# comment` from a frontmatter value.
 *
 * The shipped template annotates fields inline, so a naive split would corrupt
 * tri-state values and break JSON.parse on array values. A `#` inside quotes or
 * inside a bracketed array is data, not a comment.
 * @param {string} raw
 * @returns {string}
 */
function stripTrailingComment(raw) {
  let quote = null;
  let depth = 0;
  for (let i = 0; i < raw.length; i += 1) {
    const ch = raw[i];
    if (quote) {
      if (ch === '\\') i += 1;
      else if (ch === quote) quote = null;
      continue;
    }
    if (ch === '"' || ch === "'") quote = ch;
    else if (ch === '[') depth += 1;
    else if (ch === ']') depth -= 1;
    else if (ch === '#' && depth === 0) return raw.slice(0, i);
  }
  return raw;
}

export function parseConfig(text) {
  const fmMatch = /^---\n([\s\S]*?)\n---\n?/.exec(text);
  if (!fmMatch) {
    return { fields: {}, body: text, userNotes: extractUserNotes(text), malformed: true };
  }

  const fields = {};
  const fmLines = fmMatch[1].split('\n');
  for (const line of fmLines) {
    // Whole-line comments never define a field, even when they contain a colon.
    if (line.trim().startsWith('#')) continue;
    const colonIdx = line.indexOf(':');
    if (colonIdx === -1) continue;
    const key = line.slice(0, colonIdx).trim();
    let val = stripTrailingComment(line.slice(colonIdx + 1)).trim();
    // Parse JSON arrays
    if (val.startsWith('[')) {
      try {
        val = JSON.stringify(JSON.parse(val));
      } catch {
        // keep as string
      }
    }
    if (key) fields[key] = val;
  }

  const body = text.slice(fmMatch[0].length);
  const userNotes = extractUserNotes(body);

  return { fields, body, userNotes };
}

/**
 * Extract user notes from body text.
 * @param {string} body
 * @returns {string|null}
 */
function extractUserNotes(body) {
  const beginIdx = body.indexOf(USER_NOTES_BEGIN);
  const endIdx = body.indexOf(USER_NOTES_END);
  if (beginIdx === -1 || endIdx === -1 || endIdx <= beginIdx) return null;
  return body.slice(beginIdx + USER_NOTES_BEGIN.length, endIdx);
}

/**
 * Three-layer merge: detected always overwrites, preference only overwrites keys present, meta overwrites.
 * Unknown/extra existing keys are carried through.
 * @param {Record<string,string>} existing
 * @param {{ detected?: Record<string,string>, preference?: Record<string,string>, meta?: Record<string,string> }} layers
 * @returns {Record<string,string>}
 */
export function mergeConfig(existing, { detected = {}, preference = {}, meta = {} } = {}) {
  const result = { ...existing };

  // detected: always overwrite
  for (const [k, v] of Object.entries(detected)) {
    result[k] = String(v);
  }

  // preference: only overwrite keys explicitly present in the passed object
  for (const [k, v] of Object.entries(preference)) {
    result[k] = String(v);
  }

  // meta: always overwrite
  for (const [k, v] of Object.entries(meta)) {
    result[k] = String(v);
  }

  return result;
}

/**
 * Upgrade config fields to the current schema version. Currently identity + fill missing schema_version.
 * Named seam for future migrations.
 * @param {Record<string,string>} fields
 * @returns {Record<string,string>}
 */
export function upgradeConfig(fields) {
  const result = { ...fields };
  if (!result.schema_version) {
    result.schema_version = String(CURRENT_SCHEMA_VERSION);
  }
  return result;
}

/**
 * Serialize config with deterministic key order: detected, preference, meta, then unknown alphabetically.
 * @param {{ fields: Record<string,string>, body?: string }} config
 * @returns {string}
 */
export function serializeConfig({ fields, body = '' }) {
  const knownOrder = [...DETECTED_FIELDS, ...PREFERENCE_FIELDS, ...META_FIELDS];
  const knownSet = new Set(knownOrder);
  const unknownKeys = Object.keys(fields).filter((k) => !knownSet.has(k)).sort();

  const orderedKeys = [...knownOrder.filter((k) => k in fields), ...unknownKeys];

  let fm = '---\n';
  for (const key of orderedKeys) {
    fm += `${key}: ${fields[key]}\n`;
  }
  fm += '---\n';

  return fm + body;
}

/**
 * Atomically write text to a file (write to temp in same dir, then rename).
 * @param {string} absPath
 * @param {string} text
 */
export function writeConfigAtomic(absPath, text) {
  const dir = path.dirname(absPath);
  const tmpPath = path.join(dir, `.codex-image-config-${process.pid}-${Date.now()}.tmp`);
  try {
    fs.mkdirSync(dir, { recursive: true });
    fs.writeFileSync(tmpPath, text, 'utf8');
    fs.renameSync(tmpPath, absPath);
  } catch (err) {
    // Clean up temp file on failure
    try { fs.unlinkSync(tmpPath); } catch { /* ignore */ }
    throw err;
  }
}

/**
 * Load config from disk or return defaults. Never throws.
 * @param {string} absPath
 * @param {Record<string,string>} defaults
 * @returns {{ fields: Record<string,string>, body: string, userNotes: string|null, usedDefaults?: boolean, corrupt?: boolean }}
 */
export function loadConfigOrDefaults(absPath, defaults = {}) {
  try {
    const text = fs.readFileSync(absPath, 'utf8');
    const parsed = parseConfig(text);
    if (parsed.malformed) {
      return { fields: { ...defaults, ...parsed.fields }, body: parsed.body, userNotes: parsed.userNotes, corrupt: true };
    }
    return parsed;
  } catch {
    return { fields: { ...defaults }, body: '', userNotes: null, usedDefaults: true };
  }
}
