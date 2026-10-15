import { createHash } from 'node:crypto';

/**
 * Produce a canonical JSON string with sorted keys (recursive).
 * Deterministic for identical logical values regardless of insertion order.
 * @param {unknown} value
 * @returns {string}
 */
export const canonicalJson = (value) => {
  if (value === null || value === undefined) return JSON.stringify(value);
  if (typeof value !== 'object') return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(',')}]`;
  const keys = Object.keys(value).sort();
  return `{${keys.map((k) => `${JSON.stringify(k)}:${canonicalJson(value[k])}`).join(',')}}`;
};

/**
 * SHA-256 hex digest of the canonical JSON representation of a value.
 * @param {unknown} value
 * @returns {string} 64-char lowercase hex
 */
export const sha256 = (value) =>
  createHash('sha256').update(canonicalJson(value), 'utf8').digest('hex');

/**
 * SHA-256 hex digest of a raw UTF-8 string (not canonical-JSON-wrapped).
 * Used for content_sha256 in done records.
 * @param {string} text
 * @returns {string}
 */
export const sha256Raw = (text) =>
  createHash('sha256').update(text, 'utf8').digest('hex');
