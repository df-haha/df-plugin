import assert from 'node:assert/strict';
import test from 'node:test';

import {
  formatLineHistory,
  parseLineHistory,
  selectLineMessages,
} from '../src/extensions/line-history.mjs';

test('parses the copied Windows LINE timestamp form with zh-TW and English date headers', () => {
  const parsed = parseLineHistory([
    '2026.09.09 星期三',
    '09:05 *王小明* 第一行 😀',
    'https://example.test/路徑',
    '',
    '第二段',
    '09:07 系統訊息：阿明加入聊天室',
    '2026.09.10 (Thursday)',
    '10:00 *Example Sender* hello',
  ].join('\n'));

  assert.equal(parsed.format, 'copied-timestamp');
  assert.deepEqual(parsed.metadata, {
    windowBounded: true,
    totalHistoryKnown: false,
  });
  assert.deepEqual(parsed.messages, [
    {
      date: '2026-09-09',
      time: '09:05',
      sender: '王小明',
      text: '第一行 😀\nhttps://example.test/路徑\n\n第二段',
      kind: 'message',
    },
    {
      date: '2026-09-09',
      time: '09:07',
      sender: null,
      text: '系統訊息：阿明加入聊天室',
      kind: 'system',
    },
    {
      date: '2026-09-10',
      time: '10:00',
      sender: 'Example Sender',
      text: 'hello',
      kind: 'message',
    },
  ]);
  assert.deepEqual(parsed.unparsedLines, []);
  assert.deepEqual(parsed.warnings, []);
});

test('parses tab exports, explicit row dates, and tab continuation lines', () => {
  const parsed = parseLineHistory([
    '2026/09/10 Thu',
    '09:30\tAlice\t第一行',
    '\t第二行 😀',
    'Bob\t10:31\tHello',
    '2026-09-11\t10:32\t陳大文\t含\tTab',
  ].join('\n'));

  assert.equal(parsed.format, 'tab-separated');
  assert.deepEqual(parsed.messages, [
    {
      date: '2026-09-10',
      time: '09:30',
      sender: 'Alice',
      text: '第一行\n第二行 😀',
      kind: 'message',
    },
    {
      date: '2026-09-10',
      time: '10:31',
      sender: 'Bob',
      text: 'Hello',
      kind: 'message',
    },
    {
      date: '2026-09-11',
      time: '10:32',
      sender: '陳大文',
      text: '含\tTab',
      kind: 'message',
    },
  ]);
});

test('keeps malformed dates and unknown chunks visible instead of inventing a date', () => {
  const parsed = parseLineHistory([
    'unrecognized prelude',
    '2026-02-30 星期一',
    '08:00 *Alice* still readable but not dated',
  ].join('\n'));

  assert.deepEqual(parsed.messages, [{
    date: null,
    time: '08:00',
    sender: 'Alice',
    text: 'still readable but not dated',
    kind: 'message',
  }]);
  assert.deepEqual(parsed.unparsedLines, [
    'unrecognized prelude',
    '2026-02-30 星期一',
  ]);
  assert.match(parsed.warnings.join('\n'), /Invalid LINE date header/);
  assert.match(parsed.warnings.join('\n'), /incomplete/);

  const unknown = parseLineHistory('plain text\nwithout a supported timestamp');
  assert.equal(unknown.format, 'unknown');
  assert.equal(unknown.messages.length, 0);
  assert.match(unknown.warnings.join('\n'), /No supported LINE history records/);

  const malformedTab = parseLineHistory('2026.09.09 星期三\n09:08\tmissing body column');
  assert.equal(malformedTab.messages.length, 0);
  assert.deepEqual(malformedTab.unparsedLines, ['09:08\tmissing body column']);
});

test('normalizes common meridiem timestamps and ignores one trailing clipboard newline', () => {
  const parsed = parseLineHistory('2026年9月10日 星期四\r\n下午 3:05 *Amy* 午後訊息\r\n');
  assert.deepEqual(parsed.messages, [{
    date: '2026-09-10',
    time: '15:05',
    sender: 'Amy',
    text: '午後訊息',
    kind: 'message',
  }]);
});

test('uses an explicit copied sender boundary without guessing a name', () => {
  const parsed = parseLineHistory('2026/09/10 Thu\n[10:01] Carol: colon-form body');
  assert.deepEqual(parsed.messages, [{
    date: '2026-09-10',
    time: '10:01',
    sender: 'Carol',
    text: 'colon-form body',
    kind: 'message',
  }]);
});

test('selects literal case-insensitive filters and the newest bounded window', () => {
  const parsed = parseLineHistory([
    '2026.09.09 星期三',
    '09:05 *ALICE* literal .* [x]',
    '09:06 *Bob* ordinary',
    '09:07 系統訊息：測試通知',
    '2026.09.10 星期四',
    '10:00 *Alice* final',
  ].join('\n'));

  assert.deepEqual(
    selectLineMessages(parsed, { query: '.*', messageLimit: 100 }).map((message) => message.text),
    ['literal .* [x]'],
  );
  assert.deepEqual(
    selectLineMessages(parsed, { sender: 'alice', date: '2026-09-09', messageLimit: 100 }).map((message) => message.time),
    ['09:05'],
  );
  assert.deepEqual(
    selectLineMessages(parsed, { kind: 'SYSTEM', messageLimit: 100 }).map((message) => message.text),
    ['系統訊息：測試通知'],
  );
  assert.deepEqual(
    selectLineMessages(parsed, { dateFrom: '2026-09-09', dateTo: '2026-09-10', messageLimit: 2 }).map((message) => message.time),
    ['09:07', '10:00'],
  );

  assert.throws(() => selectLineMessages(parsed, { date: '2026-9-9' }), /strict YYYY-MM-DD/);
  assert.throws(() => selectLineMessages(parsed, { date: '2026-02-30' }), /real calendar date/);
  assert.throws(() => selectLineMessages(parsed, { dateFrom: '2026-09-11', dateTo: '2026-09-10' }), /on or before/);
  assert.throws(() => selectLineMessages(parsed, { messageLimit: 0 }), /integer from 1 through 1000/);
  assert.throws(() => selectLineMessages(parsed, { messageLimit: 1.5 }), /integer from 1 through 1000/);
  assert.throws(() => selectLineMessages(parsed, { messageLimit: 1001 }), /integer from 1 through 1000/);
});

test('received plain sender rows and URLs remain unknown instead of fake system events or senders', () => {
  const parsed = parseLineHistory('2026.09.10 星期四\n10:54 客戶甲 報價單.pdf\n10:55 客戶乙 https://example.test/file');
  assert.deepEqual(parsed.messages.map(m => [m.sender, m.kind, m.text]), [
    [null, 'unknown', '客戶甲 報價單.pdf'],
    [null, 'unknown', '客戶乙 https://example.test/file'],
  ]);
  assert.match(parsed.warnings.join(' '), /2 timestamped row/);
});

test('bracket-timestamp URI schemes are preserved without inventing a sender', () => {
  const parsed = parseLineHistory('2026.09.10 星期四\n[10:01] https://example.test/path\n[10:02] custom+app://item/42');
  assert.deepEqual(parsed.messages.map(m => [m.sender, m.kind, m.text]), [
    [null, 'unknown', 'https://example.test/path'],
    [null, 'unknown', 'custom+app://item/42'],
  ]);
});

test('formats JSON, TXT, and CSV without changing CJK, emoji, multiline text, or formula safety', () => {
  const messages = [{
    date: '2026-09-09',
    time: '09:05',
    sender: '=SUM(A1)',
    kind: 'message',
    text: '@開頭 😀\n第二行',
  }];
  const before = structuredClone(messages);

  assert.deepEqual(JSON.parse(formatLineHistory(messages, { format: 'json' })), messages);
  assert.match(formatLineHistory(messages, { format: 'txt' }), /@開頭 😀\n第二行/);

  const csv = formatLineHistory(messages, { format: 'csv' });
  assert.match(csv, /"'=SUM\(A1\)"/);
  assert.match(csv, /"'@開頭 😀\n第二行"/);
  assert.deepEqual(messages, before);
  assert.throws(() => formatLineHistory(messages, { format: 'xml' }), /json.*txt.*csv/);
});
