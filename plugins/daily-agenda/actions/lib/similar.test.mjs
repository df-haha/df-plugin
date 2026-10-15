import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { normalizeSubject, diceCoefficient, findSimilarEvents, isoDate } from './similar.mjs';

describe('normalizeSubject', () => {
  it('lowercases and strips punctuation/whitespace', () => {
    assert.equal(normalizeSubject('  Weekly Meeting!  '), 'weeklymeeting');
  });
  it('applies NFKC normalization', () => {
    // Full-width characters normalize to half-width
    assert.equal(normalizeSubject('ＡＢＣ'), 'abc');
  });
  it('handles CJK text', () => {
    assert.equal(normalizeSubject('週會 討論'), '週會討論');
  });
});

describe('diceCoefficient', () => {
  it('returns 1 for identical strings', () => {
    assert.equal(diceCoefficient('meeting', 'meeting'), 1);
  });
  it('returns 0 for completely different strings', () => {
    assert.equal(diceCoefficient('abc', 'xyz'), 0);
  });
  it('returns high similarity for similar strings', () => {
    const result = diceCoefficient('weeklymeeting', 'weeklymeetings');
    assert.ok(result > 0.9);
  });
  it('handles CJK bigrams', () => {
    const result = diceCoefficient('週會', '週會討論');
    assert.ok(result > 0.3);
  });
  it('returns 0 for single-char inputs', () => {
    assert.equal(diceCoefficient('a', 'a'), 1); // identical single chars
    assert.equal(diceCoefficient('a', 'b'), 0); // no bigrams
  });
});

describe('isoDate', () => {
  it('extracts date in Taipei timezone', () => {
    // 23:30 UTC = 07:30+8 next day
    assert.equal(isoDate('2026-10-08T23:30:00Z'), '2026-10-09');
    assert.equal(isoDate('2026-10-09T14:00:00+08:00'), '2026-10-09');
  });
});

describe('findSimilarEvents', () => {
  it('finds exact duplicate', () => {
    const candidate = {
      start_iso: '2026-10-09T14:00:00+08:00',
      end_iso: '2026-10-09T15:00:00+08:00',
      subject: '週會',
    };
    const existing = [
      { id: 'ev-1', start: '2026-10-09T14:00:00+08:00', end: '2026-10-09T15:00:00+08:00', subject: '週會' },
    ];
    const result = findSimilarEvents(candidate, existing);
    assert.equal(result.length, 1);
    assert.equal(result[0].id, 'ev-1');
    assert.equal(result[0].similarity, 1);
  });

  it('finds similar subject with overlapping time', () => {
    const candidate = {
      start_iso: '2026-10-09T14:00:00+08:00',
      end_iso: '2026-10-09T15:00:00+08:00',
      subject: '部門週會討論',
    };
    const existing = [
      { id: 'ev-2', start: '2026-10-09T14:30:00+08:00', end: '2026-10-09T15:30:00+08:00', subject: '部門週會' },
    ];
    const result = findSimilarEvents(candidate, existing);
    assert.ok(result.length > 0, `Expected similar events, dice should be >= 0.6`);
  });

  it('finds similar subject within 60 min start delta', () => {
    const candidate = {
      start_iso: '2026-10-09T14:00:00+08:00',
      end_iso: '2026-10-09T15:00:00+08:00',
      subject: '週會',
    };
    const existing = [
      { id: 'ev-3', start: '2026-10-09T14:50:00+08:00', end: '2026-10-09T15:50:00+08:00', subject: '週會' },
    ];
    const result = findSimilarEvents(candidate, existing);
    assert.ok(result.length > 0);
  });

  it('ignores events on different days', () => {
    const candidate = {
      start_iso: '2026-10-09T14:00:00+08:00',
      end_iso: '2026-10-09T15:00:00+08:00',
      subject: '週會',
    };
    const existing = [
      { id: 'ev-4', start: '2026-10-10T14:00:00+08:00', end: '2026-10-10T15:00:00+08:00', subject: '週會' },
    ];
    const result = findSimilarEvents(candidate, existing);
    assert.equal(result.length, 0);
  });

  it('ignores events with very different subjects', () => {
    const candidate = {
      start_iso: '2026-10-09T14:00:00+08:00',
      end_iso: '2026-10-09T15:00:00+08:00',
      subject: '週會',
    };
    const existing = [
      { id: 'ev-5', start: '2026-10-09T14:00:00+08:00', end: '2026-10-09T15:00:00+08:00', subject: 'Customer onboarding training session' },
    ];
    const result = findSimilarEvents(candidate, existing);
    assert.equal(result.length, 0);
  });

  it('ignores events far apart (>60 min, no overlap)', () => {
    const candidate = {
      start_iso: '2026-10-09T09:00:00+08:00',
      end_iso: '2026-10-09T10:00:00+08:00',
      subject: '週會',
    };
    const existing = [
      { id: 'ev-6', start: '2026-10-09T14:00:00+08:00', end: '2026-10-09T15:00:00+08:00', subject: '週會' },
    ];
    const result = findSimilarEvents(candidate, existing);
    assert.equal(result.length, 0);
  });

  it('SC-5: does not create pending when duplicates found', () => {
    // This is tested at the server level in server.test.mjs
    // Here we just verify findSimilarEvents returns results
    const candidate = {
      start_iso: '2026-10-09T14:00:00+08:00',
      end_iso: '2026-10-09T15:00:00+08:00',
      subject: '週會',
    };
    const existing = [
      { id: 'ev-dup', start: '2026-10-09T14:00:00+08:00', end: '2026-10-09T15:00:00+08:00', subject: '週會' },
    ];
    const result = findSimilarEvents(candidate, existing);
    assert.equal(result.length, 1);
  });
});
