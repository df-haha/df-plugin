/**
 * Calendar event similarity detection (Q-1).
 * Same day in tz AND (overlap OR |delta_start| <= 60 min) AND subject similarity >= threshold.
 */

/** Phase 5 fixture 校準 — bigram Dice coefficient threshold. */
const SIMILARITY_THRESHOLD = 0.6;

/** Maximum start-time difference in minutes to consider "near". */
const MAX_START_DELTA_MINUTES = 60;

/**
 * Normalize a subject string for comparison: NFKC, lowercase, strip whitespace + punctuation.
 * @param {string} s
 * @returns {string}
 */
export const normalizeSubject = (s) =>
  s.normalize('NFKC').toLowerCase().replace(/[\s\p{P}\p{S}]+/gu, '');

/**
 * Extract character bigrams from a normalized string.
 * CJK-safe because each CJK character becomes a pair.
 * @param {string} s
 * @returns {string[]}
 */
const bigrams = (s) => {
  const result = [];
  const chars = [...s]; // handle surrogate pairs
  for (let i = 0; i < chars.length - 1; i++) {
    result.push(chars[i] + chars[i + 1]);
  }
  return result;
};

/**
 * Sorensen-Dice coefficient on character bigrams.
 * @param {string} a - normalized
 * @param {string} b - normalized
 * @returns {number} 0..1
 */
export const diceCoefficient = (a, b) => {
  if (a === b) return 1;
  const ba = bigrams(a);
  const bb = bigrams(b);
  if (ba.length === 0 || bb.length === 0) return 0;
  const setB = new Map();
  for (const g of bb) setB.set(g, (setB.get(g) || 0) + 1);
  let matches = 0;
  for (const g of ba) {
    const count = setB.get(g);
    if (count && count > 0) {
      matches++;
      setB.set(g, count - 1);
    }
  }
  return (2 * matches) / (ba.length + bb.length);
};

/**
 * Parse an ISO datetime string to epoch ms, handling timezones.
 * @param {string} iso
 * @returns {number}
 */
const toEpoch = (iso) => new Date(iso).getTime();

/**
 * Extract the date portion (YYYY-MM-DD) from an ISO datetime in a given timezone offset.
 * For simplicity we use the numeric offset from the ISO string if present, otherwise UTC+8.
 * @param {string} iso
 * @returns {string} YYYY-MM-DD
 */
export const isoDate = (iso) => {
  // Use the Date object's timezone handling
  const d = new Date(iso);
  // Convert to Asia/Taipei (UTC+8) for day comparison
  const taipeiOffset = 8 * 60 * 60 * 1000;
  const taipeiDate = new Date(d.getTime() + taipeiOffset);
  return taipeiDate.toISOString().slice(0, 10);
};

/**
 * Check if two time ranges overlap.
 * @param {number} s1 - start1 epoch ms
 * @param {number} e1 - end1 epoch ms
 * @param {number} s2 - start2 epoch ms
 * @param {number} e2 - end2 epoch ms
 * @returns {boolean}
 */
const overlaps = (s1, e1, s2, e2) => s1 < e2 && s2 < e1;

/**
 * Find candidate duplicate events from a list.
 * @param {{start_iso: string, end_iso: string, subject: string}} candidate
 * @param {Array<{start: string, end: string, subject: string, id: string}>} existing
 * @returns {Array<{id: string, subject: string, start: string, similarity: number}>}
 */
export const findSimilarEvents = (candidate, existing) => {
  const normCandidate = normalizeSubject(candidate.subject);
  const candStart = toEpoch(candidate.start_iso);
  const candEnd = toEpoch(candidate.end_iso);
  const candDate = isoDate(candidate.start_iso);

  const duplicates = [];
  for (const event of existing) {
    // Same day check
    if (isoDate(event.start) !== candDate) continue;

    const evStart = toEpoch(event.start);
    const evEnd = toEpoch(event.end);

    // Time proximity: overlap OR start times within 60 minutes
    const startDeltaMin = Math.abs(candStart - evStart) / 60000;
    if (!overlaps(candStart, candEnd, evStart, evEnd) && startDeltaMin > MAX_START_DELTA_MINUTES) {
      continue;
    }

    // Subject similarity
    const normEvent = normalizeSubject(event.subject);
    const similarity = diceCoefficient(normCandidate, normEvent);
    if (similarity >= SIMILARITY_THRESHOLD) {
      duplicates.push({
        id: event.id,
        subject: event.subject,
        start: event.start,
        similarity: Math.round(similarity * 100) / 100,
      });
    }
  }
  return duplicates;
};
