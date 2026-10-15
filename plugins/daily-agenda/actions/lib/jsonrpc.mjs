/**
 * Minimal JSON-RPC 2.0 framing for stdio MCP.
 * Newline-delimited (NDJSON). Strips \r for cross-platform safety.
 */

/**
 * Parse a single line of NDJSON JSON-RPC input.
 * @param {string} line
 * @returns {object | null} parsed message, or null for empty/invalid lines
 */
export const parseMessage = (line) => {
  const trimmed = line.replace(/\r/g, '').trim();
  if (!trimmed) return null;
  try {
    const msg = JSON.parse(trimmed);
    if (typeof msg !== 'object' || msg === null || Array.isArray(msg)) return null;
    return msg;
  } catch {
    return null;
  }
};

/**
 * Build a JSON-RPC 2.0 success response.
 * @param {string | number} id
 * @param {unknown} result
 * @returns {string} NDJSON line
 */
export const successResponse = (id, result) =>
  JSON.stringify({ jsonrpc: '2.0', id, result });

/**
 * Build a JSON-RPC 2.0 error response.
 * @param {string | number | null} id
 * @param {number} code
 * @param {string} message
 * @param {unknown} [data]
 * @returns {string} NDJSON line
 */
export const errorResponse = (id, code, message, data) => {
  const err = { code, message };
  if (data !== undefined) err.data = data;
  return JSON.stringify({ jsonrpc: '2.0', id, error: err });
};

// Standard JSON-RPC error codes
export const METHOD_NOT_FOUND = -32601;
export const INVALID_PARAMS = -32602;
export const INTERNAL_ERROR = -32603;
export const PARSE_ERROR = -32700;

/**
 * Create a line-buffered reader from a readable stream.
 * Yields complete lines as they arrive.
 * @param {import('node:stream').Readable} stream
 * @param {(line: string) => void} onLine
 * @returns {{ stop: () => void }}
 */
export const createLineReader = (stream, onLine) => {
  let buffer = '';
  const onData = (chunk) => {
    buffer += chunk.toString('utf8');
    const lines = buffer.split('\n');
    buffer = lines.pop(); // keep incomplete last line
    for (const line of lines) {
      if (line.trim()) onLine(line);
    }
  };
  stream.on('data', onData);
  return {
    stop: () => stream.removeListener('data', onData),
  };
};
