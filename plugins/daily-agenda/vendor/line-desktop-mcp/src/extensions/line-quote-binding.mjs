import { LineToolError, requireText } from './line-runtime.mjs';

const SOURCE_FIELDS = Object.freeze(['sourceRef', 'text', 'sender', 'date', 'time']);
const ISO_DATE = /^(\d{4})-(\d{2})-(\d{2})$/u;
const REPLY_TIME = /^(\d{2}):(\d{2})(?::(\d{2}))?$/u;

/**
 * Validate the local record the caller intends to bind to a visually selected
 * LINE bubble. A sourceRef remains a local linkage: this module deliberately
 * does not treat it as a value that LINE UI can expose or confirm.
 */
export function requireReplySource(value, name = 'source') {
  if (!isRecord(value) || !hasExactKeys(value, SOURCE_FIELDS)) {
    throw invalid(`${name} must contain exactly sourceRef, text, sender, date, and time.`);
  }
  const sourceRef = value.sourceRef;
  if (typeof sourceRef !== 'string' || !/^message:[A-Za-z0-9_-]{1,128}$/u.test(sourceRef)) {
    throw invalid(`${name}.sourceRef must be one bounded local message reference.`);
  }
  const text = requireText(value.text, `${name}.text`, 10_000);
  const sender = requireSingleLineText(value.sender, `${name}.sender`, 200);
  const date = requireIsoDate(value.date, `${name}.date`);
  const time = requireReplyTime(value.time, `${name}.time`);
  return { sourceRef, text, sender, date, time };
}

export function sameReplySource(expected, observed) {
  return SOURCE_FIELDS.every(field => expected?.[field] === observed?.[field]);
}

/**
 * The selection is expressed in pixels relative to the screenshot returned by
 * getReplySourceTarget. It must be entirely inside the known message area and
 * cannot fall on the header, composer, sidebar, or transient menu area.
 */
export function requireReplySourceSelection({ sourceRect, sourcePoint } = {}, {
  imageSize,
  messageBounds,
} = {}) {
  const image = requireBounds(imageSize, 'imageSize', { originAllowed: true });
  if (image.x !== 0 || image.y !== 0) {
    throw new TypeError('imageSize must start at 0,0.');
  }
  const message = requireBounds(messageBounds, 'messageBounds', { originAllowed: true });
  if (!rectInside(message, image)) {
    throw invalid('The LINE message area was outside the captured screenshot.');
  }
  const rect = requireBounds(sourceRect, 'sourceRect', { originAllowed: true });
  if (!rectInside(rect, message)) {
    throw invalid('sourceRect must be wholly inside the displayed LINE message area.');
  }
  const point = requirePoint(sourcePoint, 'sourcePoint');
  if (!pointInside(point, rect, { strict: true })) {
    throw invalid('sourcePoint must be inside sourceRect, away from its edge.');
  }
  return { sourceRect: rect, sourcePoint: point };
}

/**
 * When accessibility exposes exact message bubbles, a visual point must select
 * one and only one of them. No bubbles is allowed for the current custom-drawn
 * LINE UI path; more than one containing bubble is an explicit refusal.
 */
export function selectAccessibleReplyBubble(candidates, sourcePoint) {
  if (!Array.isArray(candidates)) throw new TypeError('candidates must be an array.');
  const usable = candidates.filter(candidate => isRecord(candidate)
    && isRecord(candidate.element)
    && validBounds(candidate.bounds));
  if (usable.length === 0) return undefined;
  const selected = usable.filter(candidate => pointInside(sourcePoint, candidate.bounds));
  if (selected.length !== 1) {
    throw new LineToolError(
      selected.length > 1 ? 'LINE_REPLY_SOURCE_AMBIGUOUS' : 'LINE_REPLY_SOURCE_UNVERIFIED',
      selected.length > 1
        ? 'More than one accessible LINE message bubble contains the visually selected source point.'
        : 'The visually selected source point did not fall inside one accessible LINE message bubble.',
      { candidateCount: selected.length },
    );
  }
  return selected[0].element;
}

function requireSingleLineText(value, name, max) {
  requireText(value, name, max);
  if (value !== value.trim() || /[\r\n\t]/u.test(value)) {
    throw invalid(`${name} must be an exact single-line value without surrounding whitespace.`);
  }
  return value;
}

function requireIsoDate(value, name) {
  if (typeof value !== 'string') throw invalid(`${name} must be a real YYYY-MM-DD date.`);
  const match = ISO_DATE.exec(value);
  if (!match || match[0] !== value || !isRealCalendarDate(...match.slice(1).map(Number))) {
    throw invalid(`${name} must be a real YYYY-MM-DD date.`);
  }
  return value;
}

function requireReplyTime(value, name) {
  if (typeof value !== 'string') throw invalid(`${name} must use HH:mm or HH:mm:ss.`);
  const match = REPLY_TIME.exec(value);
  if (!match || match[0] !== value) throw invalid(`${name} must use HH:mm or HH:mm:ss.`);
  const hour = Number(match[1]);
  const minute = Number(match[2]);
  const second = match[3] === undefined ? undefined : Number(match[3]);
  if (hour > 23 || minute > 59 || (second !== undefined && second > 59)) {
    throw invalid(`${name} must be a real 24-hour time.`);
  }
  return value;
}

function isRealCalendarDate(year, month, day) {
  if (![year, month, day].every(Number.isInteger) || year < 1 || month < 1 || month > 12 || day < 1 || day > 31) return false;
  const date = new Date(0);
  date.setUTCFullYear(year, month - 1, day);
  date.setUTCHours(0, 0, 0, 0);
  return date.getUTCFullYear() === year && date.getUTCMonth() === month - 1 && date.getUTCDate() === day;
}

function requireBounds(value, name, { originAllowed } = {}) {
  if (!isRecord(value) || !hasExactKeys(value, ['x', 'y', 'width', 'height'])) {
    throw invalid(`${name} must contain exactly integer x, y, width, and height.`);
  }
  const bounds = {
    x: requireCoordinate(value.x, `${name}.x`),
    y: requireCoordinate(value.y, `${name}.y`),
    width: requirePositiveInteger(value.width, `${name}.width`),
    height: requirePositiveInteger(value.height, `${name}.height`),
  };
  if (!originAllowed && (bounds.x === 0 || bounds.y === 0)) {
    throw invalid(`${name} must not start on an unverified screenshot edge.`);
  }
  return bounds;
}

function requirePoint(value, name) {
  if (!isRecord(value) || !hasExactKeys(value, ['x', 'y'])) {
    throw invalid(`${name} must contain exactly integer x and y.`);
  }
  return { x: requireCoordinate(value.x, `${name}.x`), y: requireCoordinate(value.y, `${name}.y`) };
}

function requireCoordinate(value, name) {
  if (!Number.isInteger(value) || value < 0 || value > 100_000) {
    throw invalid(`${name} must be a nonnegative integer.`);
  }
  return value;
}

function requirePositiveInteger(value, name) {
  if (!Number.isInteger(value) || value < 1 || value > 100_000) {
    throw invalid(`${name} must be a positive integer.`);
  }
  return value;
}

function validBounds(value) {
  return isRecord(value)
    && ['x', 'y', 'width', 'height'].every(key => Number.isInteger(value[key]))
    && value.x >= 0 && value.y >= 0 && value.width > 0 && value.height > 0;
}

function rectInside(inner, outer) {
  return inner.x >= outer.x
    && inner.y >= outer.y
    && inner.x + inner.width <= outer.x + outer.width
    && inner.y + inner.height <= outer.y + outer.height;
}

function pointInside(point, rect, { strict = false } = {}) {
  const margin = strict ? 1 : 0;
  return point.x >= rect.x + margin
    && point.y >= rect.y + margin
    && point.x < rect.x + rect.width - margin
    && point.y < rect.y + rect.height - margin;
}

function hasExactKeys(value, expected) {
  const keys = Object.keys(value).sort();
  return keys.length === expected.length && expected.every(key => keys.includes(key));
}

function isRecord(value) {
  return !!value && typeof value === 'object' && !Array.isArray(value);
}

function invalid(message) {
  return new LineToolError('LINE_INVALID_ARGUMENT', message);
}
