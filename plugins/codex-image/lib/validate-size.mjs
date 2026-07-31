/**
 * validate-size.mjs — Size constraint validation for codex-image.
 *
 * Constraints come from image-api.md and self-scope to "fallback CLI mode only".
 * In builtin mode they are advisory (ok is always true for parseable input).
 */

/** Frozen size constraint constants. */
export const SIZE_CONSTRAINTS = Object.freeze({
  maxEdge: 3840,
  multiple: 16,
  maxRatio: 3,
  minPixels: 655360,
  maxPixels: 8294400,
});

/**
 * Snap a value to the nearest multiple of 16.
 * Tie-break: when equidistant (remainder === 8), round UP (toward the larger multiple).
 * Example: 1080 → remainder 8 → snaps UP to 1088, not 1072.
 * @param {number} v
 * @returns {number}
 */
function snapTo16(v) {
  const remainder = v % 16;
  if (remainder === 0) return v;
  // Tie-break: remainder === 8 → round UP
  if (remainder >= 9) return v + (16 - remainder);
  if (remainder <= 7) return v - remainder;
  // remainder === 8 exactly → round up
  return v + 8;
}

/**
 * Clamp a value within [16, 3840] and snap to 16.
 * @param {number} v
 * @returns {number}
 */
function clampAndSnap(v) {
  let clamped = Math.max(16, Math.min(SIZE_CONSTRAINTS.maxEdge, v));
  return snapTo16(clamped);
}

/**
 * Compute a suggested legal size from the requested dimensions.
 * @param {number} w
 * @param {number} h
 * @returns {{ width: number, height: number }}
 */
function computeSuggestion(w, h) {
  // Step 1: snap each edge to nearest multiple of 16, clamp to [16, 3840]
  let sw = clampAndSnap(w);
  let sh = clampAndSnap(h);

  // Step 2: fix ratio — shrink the long edge toward 3:1
  const longEdge = Math.max(sw, sh);
  const shortEdge = Math.min(sw, sh);
  if (shortEdge > 0 && longEdge / shortEdge > SIZE_CONSTRAINTS.maxRatio) {
    const maxLong = shortEdge * SIZE_CONSTRAINTS.maxRatio;
    if (sw >= sh) {
      sw = clampAndSnap(maxLong);
    } else {
      sh = clampAndSnap(maxLong);
    }
  }

  // Step 3: fix pixel bounds — scale both edges, re-snapping to 16
  let pixels = sw * sh;
  if (pixels < SIZE_CONSTRAINTS.minPixels) {
    const scale = Math.sqrt(SIZE_CONSTRAINTS.minPixels / pixels);
    sw = clampAndSnap(Math.ceil(sw * scale));
    sh = clampAndSnap(Math.ceil(sh * scale));
  }
  pixels = sw * sh;
  if (pixels > SIZE_CONSTRAINTS.maxPixels) {
    const scale = Math.sqrt(SIZE_CONSTRAINTS.maxPixels / pixels);
    sw = clampAndSnap(Math.floor(sw * scale));
    sh = clampAndSnap(Math.floor(sh * scale));
  }

  return { width: sw, height: sh };
}

/**
 * Validate a size input string.
 * @param {string} input — 'auto' or 'WIDTHxHEIGHT'
 * @param {{ mode?: 'builtin' | 'cli' }} [opts]
 * @returns {{ ok: boolean, mode: string, requested: {width:number,height:number}|null, violations: string[], suggestion: {width:number,height:number}|null, advisory: boolean }}
 */
export function validateSize(input, opts = {}) {
  const mode = opts.mode ?? 'builtin';

  // 'auto' is always valid
  if (input === 'auto') {
    return {
      ok: true,
      mode,
      requested: null,
      violations: [],
      suggestion: null,
      advisory: mode === 'builtin',
    };
  }

  // Parse WIDTHxHEIGHT (accept x or X as separator)
  const match = /^(\d+)\s*[xX]\s*(\d+)$/.exec(String(input).trim());
  if (!match) {
    return {
      ok: false,
      mode,
      requested: null,
      violations: ['malformed'],
      suggestion: null,
      advisory: false,
    };
  }

  const width = Number(match[1]);
  const height = Number(match[2]);

  // Zero or negative → malformed
  if (width <= 0 || height <= 0 || !Number.isInteger(width) || !Number.isInteger(height)) {
    return {
      ok: false,
      mode,
      requested: { width, height },
      violations: ['malformed'],
      suggestion: null,
      advisory: false,
    };
  }

  const violations = [];
  const { maxEdge, multiple, maxRatio, minPixels, maxPixels } = SIZE_CONSTRAINTS;

  if (width % multiple !== 0 || height % multiple !== 0) {
    violations.push(`edges must be multiples of ${multiple}`);
  }
  if (width > maxEdge || height > maxEdge) {
    violations.push(`max edge is ${maxEdge}`);
  }
  const longE = Math.max(width, height);
  const shortE = Math.min(width, height);
  if (shortE > 0 && longE / shortE > maxRatio) {
    violations.push(`long:short ratio exceeds ${maxRatio}:1`);
  }
  const pixels = width * height;
  if (pixels < minPixels) {
    violations.push(`total pixels ${pixels} below minimum ${minPixels}`);
  }
  if (pixels > maxPixels) {
    violations.push(`total pixels ${pixels} exceeds maximum ${maxPixels}`);
  }

  const suggestion = violations.length > 0 ? computeSuggestion(width, height) : null;

  if (mode === 'builtin') {
    return {
      ok: true,
      mode,
      requested: { width, height },
      violations,
      suggestion,
      advisory: true,
    };
  }

  // cli mode
  return {
    ok: violations.length === 0,
    mode,
    requested: { width, height },
    violations,
    suggestion,
    advisory: false,
  };
}
