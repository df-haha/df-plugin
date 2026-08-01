/**
 * parse-events.mjs — JSONL event parsing and heuristics for codex-image.
 */
import fs from 'node:fs';

/**
 * Parse JSONL text into events. Tolerates truncated final lines and blank lines.
 * Never throws.
 * @param {string} jsonlText
 * @returns {{ events: object[], malformedLines: number }}
 */
export function parseEvents(jsonlText) {
  const events = [];
  let malformedLines = 0;
  const lines = jsonlText.split('\n');

  for (const line of lines) {
    const trimmed = line.trim();
    if (trimmed === '') continue;
    try {
      events.push(JSON.parse(trimmed));
    } catch {
      malformedLines++;
    }
  }

  return { events, malformedLines };
}

/**
 * Check if any event indicates turn completion.
 * Handles both {type:'turn.completed'} and {msg:{type:'turn.completed'}}.
 * @param {object[]} events
 * @returns {boolean}
 */
export function hasTurnCompleted(events) {
  for (const evt of events) {
    if (evt && evt.type === 'turn.completed') return true;
    if (evt && evt.msg && evt.msg.type === 'turn.completed') return true;
  }
  return false;
}

/**
 * Patterns that suggest code-based image drawing.
 * Only matched against command-execution events, never prompt text.
 * @type {RegExp[]}
 */
const CODE_DRAWING_PATTERNS = [
  /\bPIL\b/,
  /\bPillow\b/,
  /\bmatplotlib\b/,
  /\bcairosvg\b/,
  /\bImageDraw\b/,
  /\bdraw\.\w/,
  /\bimg\.save\(/,
];

/**
 * Check if an event is a command-execution event (not a prompt or assistant message).
 * @param {object} evt
 * @returns {boolean}
 */
function isCommandEvent(evt) {
  if (!evt || typeof evt !== 'object') return false;
  // Check type field for command execution patterns
  if (typeof evt.type === 'string') {
    if (evt.type.includes('command_execution') || evt.type.includes('exec_command')) {
      return true;
    }
  }
  // Check for command or parsed_cmd fields
  if (evt.command !== undefined || evt.parsed_cmd !== undefined) return true;
  return false;
}

/**
 * Extract command text from a command-execution event.
 * @param {object} evt
 * @returns {string}
 */
function extractCommandText(evt) {
  const parts = [];
  if (typeof evt.command === 'string') parts.push(evt.command);
  if (typeof evt.parsed_cmd === 'string') parts.push(evt.parsed_cmd);
  // Also check nested content
  if (typeof evt.content === 'string') parts.push(evt.content);
  if (typeof evt.output === 'string') parts.push(evt.output);
  return parts.join(' ');
}

/**
 * Detect code-based image drawing in command-execution events.
 * Only inspects command events, never prompt text or assistant messages.
 * @param {object[]} events
 * @returns {{ suspected: boolean, evidence: string[], heuristic: true }}
 */
export function detectCodeDrawing(events) {
  const evidence = [];

  for (const evt of events) {
    if (!isCommandEvent(evt)) continue;
    const text = extractCommandText(evt);
    if (!text) continue;

    for (const pattern of CODE_DRAWING_PATTERNS) {
      if (pattern.test(text)) {
        evidence.push(`Matched ${pattern} in command event`);
      }
    }
  }

  return {
    suspected: evidence.length > 0,
    evidence,
    heuristic: true,
  };
}

/** PNG 8-byte signature */
const PNG_SIGNATURE = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]);

/**
 * Read width and height from a PNG IHDR chunk in a buffer.
 * @param {Buffer} buffer — at least 24 bytes
 * @returns {{ width: number, height: number } | null}
 */
export function readPngSize(buffer) {
  if (!Buffer.isBuffer(buffer) || buffer.length < 24) return null;

  // Verify PNG signature
  for (let i = 0; i < 8; i++) {
    if (buffer[i] !== PNG_SIGNATURE[i]) return null;
  }

  // IHDR starts at byte 8: 4 bytes length, 4 bytes 'IHDR', then width (4) + height (4)
  // Bytes 16..19 = width, 20..23 = height (big-endian)
  const width = buffer.readUInt32BE(16);
  const height = buffer.readUInt32BE(20);
  return { width, height };
}

/**
 * Read PNG dimensions from a file, reading only the first 32 bytes.
 * @param {string} absPath
 * @returns {{ width: number, height: number } | null}
 */
export function readPngSizeFromFile(absPath) {
  let fd;
  try {
    fd = fs.openSync(absPath, 'r');
    const buf = Buffer.alloc(32);
    const bytesRead = fs.readSync(fd, buf, 0, 32, 0);
    if (bytesRead < 24) return null;
    return readPngSize(buf);
  } catch {
    return null;
  } finally {
    if (fd !== undefined) fs.closeSync(fd);
  }
}
