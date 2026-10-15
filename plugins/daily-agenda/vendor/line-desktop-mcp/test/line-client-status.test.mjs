import assert from 'node:assert/strict';
import test from 'node:test';
import { parseLineClientStatus } from '../src/extensions/line-client-status.mjs';

const known = () => ({ ok: true, client: { verified: true, version: '26.4.2.3957',
  buildRef: 'sha256:' + 'a'.repeat(64) }, process: { state: 'running',
  processInstanceRef: 'process:' + 'b'.repeat(64) } });
const failed = () => ({ ok: false, code: 'LINE_BUILD_UNVERIFIED', client: { verified: false },
  process: { state: 'not_checked' } });
const parse = (code, value) => parseLineClientStatus(code, JSON.stringify(value));

test('closed build/process status distinguishes failure, absence, ambiguity and a verified process', () => {
  assert.deepEqual(parse(0, known()), known());
  assert.deepEqual(parse(2, failed()), failed());
  for (const process of [{ state: 'not_available' }, { state: 'not_running' },
    { state: 'unverified' }, { state: 'ambiguous', processCount: 2 }]) {
    const value = { ...known(), process };
    assert.deepEqual(parse(0, value), value);
  }
});

test('status rejects extra data, unverified process claims and contradictory child exits', () => {
  for (const [code, value] of [
    [2, known()], [0, failed()], [1, failed()],
    [0, { ...known(), path: 'private' }],
    [0, { ...known(), client: { ...known().client, path: 'private' } }],
    [0, { ...known(), process: { ...known().process, pid: 42 } }],
    [0, { ...known(), process: { state: 'running' } }],
    [0, { ...known(), process: { state: 'ambiguous', processCount: 1 } }],
    [0, { ...known(), client: { ...known().client, version: 'private/path' } }],
    [0, { ...known(), process: { state: 'not_checked' } }],
    [2, { ...failed(), code: 'unknown private error' }],
  ]) assert.equal(parse(code, value).code, 'LINE_CLIENT_STATUS_UNAVAILABLE');
  assert.equal(parseLineClientStatus(0, 'x'.repeat(8193)).code, 'LINE_CLIENT_STATUS_UNAVAILABLE');
});
