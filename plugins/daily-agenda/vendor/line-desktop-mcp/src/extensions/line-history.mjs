const ISO_DATE_PATTERN = /^\d{4}-\d{2}-\d{2}$/;
const HISTORY_DATE_TOKEN_PATTERN = /^(\d{4}(?:[./-]\d{1,2}[./-]\d{1,2}|\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日))(?=$|\s)/u;
const HISTORY_DATE_PATTERN = /^(\d{4})[./-](\d{1,2})[./-](\d{1,2})$|^(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日$/u;
const TIME_PATTERN = /^(?:(上午|下午|a\.?m\.?|p\.?m\.?)\s*)?(\d{1,2}):(\d{2})(?:\s*(上午|下午|a\.?m\.?|p\.?m\.?))?$/iu;
const TIME_PREFIX_PATTERN = /^(?:(?:上午|下午|a\.?m\.?|p\.?m\.?)\s*)?\d{1,2}:\d{2}(?:\s*(?:上午|下午|a\.?m\.?|p\.?m\.?))?(?=$|\s|[:：])/iu;

const ENGLISH_WEEKDAYS = new Map([
  ['sun', 0], ['sunday', 0],
  ['mon', 1], ['monday', 1],
  ['tue', 2], ['tues', 2], ['tuesday', 2],
  ['wed', 3], ['wednesday', 3],
  ['thu', 4], ['thur', 4], ['thurs', 4], ['thursday', 4],
  ['fri', 5], ['friday', 5],
  ['sat', 6], ['saturday', 6],
]);

const ZH_TW_WEEKDAYS = new Map([
  ['星期日', 0], ['星期天', 0], ['週日', 0], ['週天', 0], ['周日', 0], ['周天', 0],
  ['星期一', 1], ['週一', 1], ['周一', 1],
  ['星期二', 2], ['週二', 2], ['周二', 2],
  ['星期三', 3], ['週三', 3], ['周三', 3],
  ['星期四', 4], ['週四', 4], ['周四', 4],
  ['星期五', 5], ['週五', 5], ['周五', 5],
  ['星期六', 6], ['週六', 6], ['周六', 6],
]);

/**
 * Parse a copied LINE history window without claiming that it represents the
 * full chat. Recognized records are deliberately conservative: the current
 * Windows reader returns clipboard text, so source text outside the supported
 * structures remains visible through `unparsedLines` and `warnings`.
 *
 * Supported structures include:
 * - `2026.09.09 星期三` / `2026/09/09 (Wed)` date headers
 * - `09:05 *Alice* message` copied LINE timestamp rows and their following
 *   multiline/blank-line continuations
 * - tab exports such as `09:05\tAlice\tmessage` and
 *   `2026-09-09\t09:05\tAlice\tmessage`
 *
 * @param {string} text
 * @returns {{
 *   messages: Array<{date: string | null, time: string, sender: string | null, text: string, kind: 'message' | 'system' | 'unknown'}>,
 *   unparsedLines: string[],
 *   warnings: string[],
 *   format: 'tab-separated' | 'copied-timestamp' | 'mixed' | 'unknown',
 *   metadata: {windowBounded: true, totalHistoryKnown: false}
 * }}
 */
export function parseLineHistory(text) {
  if (typeof text !== 'string') {
    throw new TypeError('LINE history text must be a string.');
  }

  const normalizedText = text.replace(/\r\n?/g, '\n');
  // Clipboard text commonly ends in one transport newline. Drop that delimiter
  // without dropping intentional blank continuation lines before it.
  const lines = (normalizedText.endsWith('\n') ? normalizedText.slice(0, -1) : normalizedText).split('\n');
  const messages = [];
  const unparsedLines = [];
  const warnings = [];
  const formats = new Set();
  let currentDate = null;
  let index = 0;

  while (index < lines.length) {
    const line = lines[index];
    const header = parseDateHeader(line);

    if (header) {
      if (header.invalidDate) {
        currentDate = null;
        unparsedLines.push(line);
        warnings.push(`Invalid LINE date header at line ${index + 1}: ${header.rawDate}.`);
      } else {
        currentDate = header.date;
        if (header.weekday !== null && weekdayForDate(header.date) !== header.weekday) {
          warnings.push(`LINE date header weekday does not match ${header.date} at line ${index + 1}.`);
        }
      }
      index += 1;
      continue;
    }

    if (line.trim() === '') {
      index += 1;
      continue;
    }

    const tabRecord = parseTabRecord(line, currentDate);
    if (tabRecord?.invalidDate) {
      unparsedLines.push(line);
      warnings.push(`Invalid LINE message date at line ${index + 1}: ${tabRecord.invalidDate}.`);
      index += 1;
      continue;
    }
    if (tabRecord) {
      const message = tabRecord.message;
      let next = index + 1;
      while (next < lines.length && lines[next].startsWith('\t')) {
        message.text += `\n${lines[next].slice(1)}`;
        next += 1;
      }
      messages.push(message);
      formats.add('tab-separated');
      index = next;
      continue;
    }

    const timestampRecord = parseTimestampRecord(line, currentDate);
    if (timestampRecord?.invalidDate) {
      unparsedLines.push(line);
      warnings.push(`Invalid LINE message date at line ${index + 1}: ${timestampRecord.invalidDate}.`);
      index += 1;
      continue;
    }
    if (timestampRecord) {
      const message = timestampRecord.message;
      const continuation = consumeCopiedContinuation(lines, index + 1, currentDate);
      if (continuation.lines.length > 0) {
        message.text += `\n${continuation.lines.join('\n')}`;
      }
      messages.push(message);
      formats.add('copied-timestamp');
      index = continuation.nextIndex;
      continue;
    }

    if (line.trim() !== '') {
      unparsedLines.push(line);
    }
    index += 1;
  }

  if (messages.length === 0 && text.trim() !== '') {
    warnings.push('No supported LINE history records were found; the input was not treated as complete history.');
  }
  if (unparsedLines.length > 0) {
    warnings.push(`Skipped ${unparsedLines.length} unparsed LINE history line(s); parsed messages are incomplete.`);
  }
  const unknownSenderCount = messages.filter(message => message.kind === 'unknown').length;
  if (unknownSenderCount) warnings.push(`${unknownSenderCount} timestamped row(s) have no explicit sender boundary; their original text is preserved and their kind is unknown.`);

  return {
    messages,
    unparsedLines,
    warnings,
    format: resolveFormat(formats),
    metadata: {
      windowBounded: true,
      totalHistoryKnown: false,
    },
  };
}

/**
 * Select parsed records from the copied window. `messageLimit` selects the
 * newest matching records because copied LINE text is chronological.
 *
 * @param {{messages: Array<{date: string | null, time: string, sender: string | null, text: string, kind: string}>}} parsed
 * @param {{date?: string, dateFrom?: string, dateTo?: string, messageLimit?: number, query?: string, sender?: string, kind?: string}} [options]
 * @returns {Array<{date: string | null, time: string, sender: string | null, text: string, kind: string}>}
 */
export function selectLineMessages(parsed, options = {}) {
  if (!parsed || !Array.isArray(parsed.messages)) {
    throw new TypeError('parsed must be a parseLineHistory result with a messages array.');
  }
  if (!options || typeof options !== 'object' || Array.isArray(options)) {
    throw new TypeError('LINE history selection options must be an object.');
  }

  const date = validateOptionalIsoDate(options.date, 'date');
  const dateFrom = validateOptionalIsoDate(options.dateFrom, 'dateFrom');
  const dateTo = validateOptionalIsoDate(options.dateTo, 'dateTo');
  if (dateFrom && dateTo && dateFrom > dateTo) {
    throw new RangeError('dateFrom must be on or before dateTo.');
  }

  const messageLimit = validateMessageLimit(options.messageLimit);
  const query = normalizeOptionalSearch(options.query, 'query');
  const sender = normalizeOptionalSearch(options.sender, 'sender');
  const kind = normalizeOptionalSearch(options.kind, 'kind');

  const selected = parsed.messages.filter((message) => {
    if (!message || typeof message !== 'object') return false;
    const messageDate = typeof message.date === 'string' ? message.date : null;
    if (date && messageDate !== date) return false;
    if (dateFrom && (!messageDate || messageDate < dateFrom)) return false;
    if (dateTo && (!messageDate || messageDate > dateTo)) return false;
    if (query && !lower(message.text).includes(query)) return false;
    if (sender && !lower(message.sender).includes(sender)) return false;
    if (kind && !lower(message.kind).includes(kind)) return false;
    return true;
  });

  return selected.slice(-messageLimit);
}

/**
 * Format selected LINE messages without I/O or mutation.
 *
 * @param {Array<{date?: string | null, time?: string | null, sender?: string | null, text?: string | null, kind?: string | null}>} messages
 * @param {{format?: 'json' | 'txt' | 'csv'}} [options]
 * @returns {string}
 */
export function formatLineHistory(messages, { format = 'json' } = {}) {
  if (!Array.isArray(messages)) {
    throw new TypeError('messages must be an array.');
  }
  if (!['json', 'txt', 'csv'].includes(format)) {
    throw new RangeError("format must be one of 'json', 'txt', or 'csv'.");
  }

  if (format === 'json') {
    return JSON.stringify(messages, null, 2);
  }

  const columns = ['date', 'time', 'sender', 'kind', 'text'];
  if (format === 'txt') {
    return [
      columns.join('\t'),
      ...messages.map((message) => columns.map((column) => outputValue(message?.[column])).join('\t')),
    ].join('\n');
  }

  return [
    columns.join(','),
    ...messages.map((message) => columns.map((column) => csvCell(outputValue(message?.[column]))).join(',')),
  ].join('\n');
}

function consumeCopiedContinuation(lines, startIndex, currentDate) {
  const continuation = [];
  let index = startIndex;

  while (index < lines.length) {
    const line = lines[index];
    if (parseDateHeader(line) || parseTabRecord(line, currentDate) || parseTimestampRecord(line, currentDate)) {
      break;
    }
    continuation.push(line);
    index += 1;
  }

  return { lines: continuation, nextIndex: index };
}

function parseDateHeader(line) {
  if (line.includes('\t')) return null;
  const candidate = takeHistoryDatePrefix(line.trim());
  if (!candidate) return null;

  const weekday = parseWeekdayTail(candidate.rest);
  if (weekday === undefined) return null;
  if (!candidate.date) {
    return { invalidDate: true, rawDate: candidate.rawDate };
  }
  return { date: candidate.date, weekday };
}

function parseTabRecord(line, currentDate) {
  if (!line.includes('\t') || line.startsWith('\t')) return null;
  const fields = line.split('\t');
  if (fields.length < 3) return null;

  const firstDate = parseHistoryDate(fields[0].trim());
  const secondTime = parseTime(fields[1]);
  const thirdSender = normalizeSender(fields[2]);
  if (secondTime && thirdSender && looksLikeHistoryDate(fields[0])) {
    if (!firstDate) return { invalidDate: fields[0].trim() };
    return {
      message: makeMessage({ date: firstDate, time: secondTime }, thirdSender, fields.slice(3).join('\t'), 'message', currentDate),
    };
  }

  const firstStamp = parseStamp(fields[0]);
  if (firstStamp?.invalidDate) return { invalidDate: firstStamp.invalidDate };
  const secondSender = normalizeSender(fields[1]);
  if (firstStamp && secondSender) {
    return {
      message: makeMessage(firstStamp, secondSender, fields.slice(2).join('\t'), 'message', currentDate),
    };
  }

  const secondStamp = parseStamp(fields[1]);
  if (secondStamp?.invalidDate) return { invalidDate: secondStamp.invalidDate };
  const firstSender = normalizeSender(fields[0]);
  if (secondStamp && firstSender) {
    return {
      message: makeMessage(secondStamp, firstSender, fields.slice(2).join('\t'), 'message', currentDate),
    };
  }

  return null;
}

function parseTimestampRecord(line, currentDate) {
  // A tabbed line is either handled by parseTabRecord or remains unparsed;
  // never reinterpret a malformed tab export as a system notice.
  if (line.includes('\t')) return null;
  const trimmed = line.trimStart();
  if (!trimmed.trim()) return null;

  let stamp;
  let rest;
  const bracket = /^\[([^\]]+)\]\s*(.*)$/u.exec(trimmed);
  if (bracket) {
    stamp = parseStamp(bracket[1]);
    rest = bracket[2];
  } else {
    const datePrefix = takeHistoryDatePrefix(trimmed);
    if (datePrefix) {
      const timePrefix = takeTimePrefix(datePrefix.rest);
      if (!timePrefix) return null;
      if (!datePrefix.date) return { invalidDate: datePrefix.rawDate };
      stamp = { date: datePrefix.date, time: timePrefix.time };
      rest = timePrefix.rest;
    } else {
      const timePrefix = takeTimePrefix(trimmed);
      if (!timePrefix) return null;
      stamp = { date: null, time: timePrefix.time };
      rest = timePrefix.rest;
    }
  }

  if (stamp?.invalidDate) return { invalidDate: stamp.invalidDate };
  if (!stamp) return null;

  const content = rest.trimStart();
  const senderMatch = /^\*([^*\t\r\n]+)\*(?: ?(.*))?$/u.exec(content);
  if (senderMatch) {
    const sender = normalizeSender(senderMatch[1]);
    if (!sender) return null;
    return {
      message: makeMessage(stamp, sender, senderMatch[2] ?? '', 'message', currentDate),
    };
  }

  // Some copied clients use an explicit `sender: body` row instead of the
  // Windows `*sender*` marker. The colon makes the sender boundary explicit.
  const explicitSender = bracket && !isSystemNotice(content) ? parseExplicitSenderText(content) : null;
  if (explicitSender) {
    return {
      message: makeMessage(stamp, explicitSender.sender, explicitSender.text, 'message', currentDate),
    };
  }

  // Ordinary received messages/files also lack the copied *sender* delimiter.
  // Missing sender syntax is not evidence of a system event. Preserve all text.
  if (content !== '') {
    return {
      message: makeMessage(stamp, null, content, isSystemNotice(content) ? 'system' : 'unknown', currentDate),
    };
  }

  return null;
}

function parseStamp(value) {
  const time = parseTime(value);
  if (time) return { date: null, time };

  const datePrefix = takeHistoryDatePrefix(String(value).trim());
  if (!datePrefix) return null;
  const dateTime = parseTime(datePrefix.rest);
  if (!dateTime) return null;
  if (!datePrefix.date) return { invalidDate: datePrefix.rawDate };
  return { date: datePrefix.date, time: dateTime };
}

function makeMessage(stamp, sender, text, kind, currentDate) {
  return {
    date: stamp.date ?? currentDate ?? null,
    time: stamp.time,
    sender,
    text: String(text),
    kind,
  };
}

function takeHistoryDatePrefix(value) {
  const input = String(value).trimStart();
  const match = HISTORY_DATE_TOKEN_PATTERN.exec(input);
  if (!match) return null;
  return {
    rawDate: match[1],
    date: parseHistoryDate(match[1]),
    rest: input.slice(match[0].length).trimStart(),
  };
}

function looksLikeHistoryDate(value) {
  return Boolean(HISTORY_DATE_TOKEN_PATTERN.exec(String(value).trim()));
}

function parseHistoryDate(value) {
  const match = HISTORY_DATE_PATTERN.exec(String(value).trim());
  if (!match) return null;
  const year = Number(match[1] ?? match[4]);
  const month = Number(match[2] ?? match[5]);
  const day = Number(match[3] ?? match[6]);
  if (!isRealDate(year, month, day)) return null;
  return `${String(year).padStart(4, '0')}-${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
}

function parseWeekdayTail(value) {
  let tail = String(value).trim();
  if (tail === '') return null;
  if ((tail.startsWith('(') && tail.endsWith(')')) || (tail.startsWith('（') && tail.endsWith('）'))) {
    tail = tail.slice(1, -1).trim();
  } else if (tail.includes('(') || tail.includes(')') || tail.includes('（') || tail.includes('）')) {
    return undefined;
  }

  const english = tail.toLowerCase().replace(/\.$/u, '');
  if (ENGLISH_WEEKDAYS.has(english)) return ENGLISH_WEEKDAYS.get(english);
  const zhTw = tail.replace(/\s+/gu, '');
  return ZH_TW_WEEKDAYS.has(zhTw) ? ZH_TW_WEEKDAYS.get(zhTw) : undefined;
}

function parseTime(value) {
  const match = TIME_PATTERN.exec(String(value).trim());
  if (!match) return null;
  const leading = normalizeMeridiem(match[1]);
  const trailing = normalizeMeridiem(match[4]);
  if (leading && trailing && leading !== trailing) return null;

  const marker = leading ?? trailing;
  let hour = Number(match[2]);
  const minute = Number(match[3]);
  if (minute > 59) return null;

  if (marker) {
    if (hour < 1 || hour > 12) return null;
    if (marker === 'am' && hour === 12) hour = 0;
    if (marker === 'pm' && hour !== 12) hour += 12;
  } else if (hour > 23) {
    return null;
  }

  return `${String(hour).padStart(2, '0')}:${String(minute).padStart(2, '0')}`;
}

function takeTimePrefix(value) {
  const input = String(value).trimStart();
  const match = TIME_PREFIX_PATTERN.exec(input);
  if (!match) return null;
  const time = parseTime(match[0]);
  if (!time) return null;
  return { time, rest: input.slice(match[0].length).trimStart() };
}

function normalizeMeridiem(value) {
  if (!value) return null;
  const normalized = String(value).toLowerCase().replace(/\./gu, '');
  if (normalized === 'am' || normalized === '上午') return 'am';
  if (normalized === 'pm' || normalized === '下午') return 'pm';
  return null;
}

function normalizeSender(value) {
  const sender = String(value ?? '').trim();
  if (!sender || sender.length > 256 || /[\t\r\n]/u.test(sender)) return null;
  return sender;
}

function parseExplicitSenderText(value) {
  if (/^[a-z][a-z\d+.-]*:\/\//iu.test(value)) return null;
  const match = /^([^:：\t\r\n]+)[:：](?: ?(.*))?$/u.exec(value);
  if (!match) return null;
  const sender = normalizeSender(match[1]);
  return sender ? { sender, text: match[2] ?? '' } : null;
}

function isSystemNotice(value) {
  return /^(?:系統(?:訊息|消息|通知)?|system(?:\s+(?:message|notice))?|line\s+system)\s*[:：]/iu.test(value);
}

function isRealDate(year, month, day) {
  if (!Number.isInteger(year) || !Number.isInteger(month) || !Number.isInteger(day) || year < 1 || month < 1 || month > 12 || day < 1) {
    return false;
  }
  const daysInMonth = [31, isLeapYear(year) ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  return day <= daysInMonth[month - 1];
}

function isLeapYear(year) {
  return year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
}

function weekdayForDate(isoDate) {
  const [year, month, day] = isoDate.split('-').map(Number);
  return new Date(Date.UTC(year, month - 1, day)).getUTCDay();
}

function resolveFormat(formats) {
  if (formats.size === 0) return 'unknown';
  if (formats.size > 1) return 'mixed';
  return formats.values().next().value;
}

function validateOptionalIsoDate(value, name) {
  if (value === undefined) return null;
  if (typeof value !== 'string' || !ISO_DATE_PATTERN.test(value)) {
    throw new TypeError(`${name} must be a strict YYYY-MM-DD date.`);
  }
  const parsed = parseHistoryDate(value);
  if (!parsed) {
    throw new RangeError(`${name} must be a real calendar date.`);
  }
  return parsed;
}

function validateMessageLimit(value) {
  if (value === undefined) return 100;
  if (!Number.isInteger(value) || value < 1 || value > 1000) {
    throw new RangeError('messageLimit must be an integer from 1 through 1000.');
  }
  return value;
}

function normalizeOptionalSearch(value, name) {
  if (value === undefined) return null;
  if (typeof value !== 'string') {
    throw new TypeError(`${name} must be a string when provided.`);
  }
  return lower(value);
}

function lower(value) {
  return String(value ?? '').toLowerCase();
}

function outputValue(value) {
  return value == null ? '' : String(value);
}

function csvCell(value) {
  const safeValue = /^\s*[=+\-@]/u.test(value) ? `'${value}` : value;
  return `"${safeValue.replace(/"/gu, '""')}"`;
}
