import assert from 'node:assert/strict';
import test from 'node:test';

import {
  requireReplySource,
  requireReplySourceSelection,
  sameReplySource,
  selectAccessibleReplyBubble,
} from '../src/extensions/line-quote-binding.mjs';

const source = Object.freeze({
  sourceRef: 'message:0123456789abcdef01234567',
  text: '完整引用來源',
  sender: '測試員',
  date: '2026-09-11',
  time: '10:22:33',
});

const imageSize = { x: 0, y: 0, width: 400, height: 600 };
const messageBounds = { x: 100, y: 50, width: 300, height: 430 };
const selection = {
  sourceRect: { x: 160, y: 180, width: 140, height: 72 },
  sourcePoint: { x: 220, y: 214 },
};

test('reply source preserves full local identity but never implies sourceRef is a UI value', () => {
  assert.deepEqual(requireReplySource(source), source);
  assert.equal(sameReplySource(source, { ...source }), true);
  assert.equal(sameReplySource(source, { ...source, sender: '另一位' }), false);
  assert.equal(sameReplySource(source, { ...source, time: '10:22:34' }), false);
  assert.equal(sameReplySource(source, { ...source, sourceRef: 'message:other' }), false);
  assert.throws(
    () => requireReplySource({ ...source, sourceRef: '../not-a-local-reference' }),
    { code: 'LINE_INVALID_ARGUMENT' },
  );
});

test('visual source selection excludes header, composer, sidebar, and screenshot-edge targets', () => {
  assert.deepEqual(
    requireReplySourceSelection(selection, { imageSize, messageBounds }),
    selection,
  );
  for (const changed of [
    { sourceRect: { x: 100, y: 24, width: 100, height: 30 } },
    { sourceRect: { x: 120, y: 470, width: 100, height: 50 } },
    { sourceRect: { x: 0, y: 100, width: 99, height: 50 } },
    { sourcePoint: { x: 160, y: 180 } },
  ]) {
    assert.throws(
      () => requireReplySourceSelection({ ...selection, ...changed }, { imageSize, messageBounds }),
      { code: 'LINE_INVALID_ARGUMENT' },
    );
  }
});

test('accessibility strengthens a visual source selection but ambiguous duplicate bubbles refuse', () => {
  const first = { id: 'first' };
  const second = { id: 'second' };
  const point = { x: 220, y: 214 };
  assert.equal(selectAccessibleReplyBubble([], point), undefined);
  assert.equal(selectAccessibleReplyBubble([
    { element: first, bounds: { x: 160, y: 180, width: 140, height: 72 } },
  ], point), first);
  assert.throws(
    () => selectAccessibleReplyBubble([
      { element: first, bounds: { x: 160, y: 180, width: 140, height: 72 } },
      { element: second, bounds: { x: 180, y: 190, width: 120, height: 72 } },
    ], point),
    { code: 'LINE_REPLY_SOURCE_AMBIGUOUS' },
  );
  assert.throws(
    () => selectAccessibleReplyBubble([
      { element: first, bounds: { x: 160, y: 180, width: 140, height: 72 } },
    ], { x: 305, y: 214 }),
    { code: 'LINE_REPLY_SOURCE_UNVERIFIED' },
  );
});
