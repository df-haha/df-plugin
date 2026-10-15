import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { canonicalJson, sha256, sha256Raw } from './canonical.mjs';

describe('canonicalJson', () => {
  it('sorts object keys', () => {
    assert.equal(canonicalJson({ b: 2, a: 1 }), '{"a":1,"b":2}');
  });

  it('handles nested objects', () => {
    const result = canonicalJson({ z: { b: 1, a: 2 }, a: [] });
    assert.equal(result, '{"a":[],"z":{"a":2,"b":1}}');
  });

  it('handles arrays preserving order', () => {
    assert.equal(canonicalJson([3, 1, 2]), '[3,1,2]');
  });

  it('handles primitives', () => {
    assert.equal(canonicalJson(null), 'null');
    assert.equal(canonicalJson(true), 'true');
    assert.equal(canonicalJson('hi'), '"hi"');
    assert.equal(canonicalJson(42), '42');
  });

  it('handles undefined', () => {
    // JSON.stringify(undefined) returns undefined (not a string)
    // Our canonicalJson returns the same
    const result = canonicalJson(undefined);
    assert.equal(result, undefined);
  });

  it('is deterministic regardless of insertion order', () => {
    const a = { x: 1, y: 2, z: 3 };
    const b = { z: 3, x: 1, y: 2 };
    assert.equal(canonicalJson(a), canonicalJson(b));
  });
});

describe('sha256', () => {
  it('returns 64-char hex digest', () => {
    const result = sha256({ hello: 'world' });
    assert.equal(result.length, 64);
    assert.match(result, /^[0-9a-f]{64}$/);
  });

  it('is deterministic', () => {
    assert.equal(sha256({ a: 1 }), sha256({ a: 1 }));
  });

  it('differs for different values', () => {
    assert.notEqual(sha256({ a: 1 }), sha256({ a: 2 }));
  });

  it('differs from sha256Raw for same string', () => {
    // sha256 wraps in canonical JSON, sha256Raw does not
    assert.notEqual(sha256('hello'), sha256Raw('hello'));
  });
});

describe('sha256Raw', () => {
  it('returns 64-char hex digest of raw string', () => {
    const result = sha256Raw('test');
    assert.equal(result.length, 64);
    assert.match(result, /^[0-9a-f]{64}$/);
  });

  it('is deterministic', () => {
    assert.equal(sha256Raw('test'), sha256Raw('test'));
  });
});
