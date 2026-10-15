import { execFile as execFileCallback } from 'node:child_process';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { promisify } from 'node:util';

import { LineToolError } from './line-runtime.mjs';

const execFile = promisify(execFileCallback);
const WINDOWS_POWERSHELL = path.join(process.env.SystemRoot || 'C:\\Windows', 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe');
const OCR_SCRIPT = fileURLToPath(new URL('./ocr-line-image.ps1', import.meta.url));
const PNG_SIGNATURE = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
const FINGERPRINT_IMAGE_MAX_BYTES = 1024 * 1024;

export const LINE_OCR_DEFAULTS = Object.freeze({
  maxBytes: 10 * 1024 * 1024,
  maxDimension: 4096,
  timeoutMs: 15_000,
  upscaleFactor: 2,
  preferredLanguages: ['zh-Hant', 'en'],
});

/**
 * Read only the validated PNG IHDR dimensions. This does not start
 * PowerShell, create files, capture a screen, or inspect LINE.
 *
 * @param {{type?: string, data?: string, mimeType?: string, image_url?: string}} imageContent
 * @returns {{width: number, height: number}}
 */
export function purepngDimensions(imageContent) {
  return readPngDimensions(decodePngImageContent(imageContent, LINE_OCR_DEFAULTS.maxBytes));
}

/**
 * OCR one already-captured PNG image locally. This function neither captures
 * a screen nor interacts with LINE; its coordinates are pixels in the input
 * PNG, so callers can use them directly against that same screenshot.
 *
 * @param {{type?: string, data?: string, mimeType?: string, image_url?: string}} imageContent
 * @param {{maxBytes?: number, maxDimension?: number, timeoutMs?: number, upscaleFactor?: number, paddingPixels?: number, preferredLanguages?: string[]}} [options]
 * @returns {Promise<{width: number, height: number, language: string, coordinateSpace: 'input-png-pixels', scaleFactor: 1, ocrScaleFactor: number, lines: Array<{text: string, words: Array<{text: string, x: number, y: number, width: number, height: number}>, x: number, y: number, width: number, height: number}>}>}
 */
export async function recognizeLineImage(imageContent, options = {}) {
  if (process.platform !== 'win32') {
    throw new LineToolError('LINE_OCR_RUNTIME_UNAVAILABLE', 'Local LINE OCR requires Windows PowerShell 5.1 and Windows.Media.Ocr.');
  }

  const config = normalizeOptions(options);
  const png = decodePngImageContent(imageContent, config.maxBytes);
  let temporaryDirectory;
  let result;
  let failure;

  try {
    temporaryDirectory = await fs.mkdtemp(path.join(os.tmpdir(), 'line-ocr-'));
    const inputPath = path.join(temporaryDirectory, 'input.png');
    await fs.writeFile(inputPath, png, { flag: 'wx' });

    const response = await invokeOcr(inputPath, config);
    result = normalizeOcrResult(response);
  } catch (error) {
    failure = error instanceof LineToolError
      ? error
      : new LineToolError(
        'LINE_OCR_TEMP_UNAVAILABLE',
        'Local OCR could not create or write its owned temporary PNG input.',
        { failureCode: error?.code || error?.name || 'unknown' },
      );
  }

  if (temporaryDirectory) {
    try {
      // This directory came from mkdtemp above; no caller-supplied path is ever removed.
      await fs.rm(temporaryDirectory, { recursive: true, force: true, maxRetries: 2 });
    } catch (cleanupError) {
      if (!failure) {
        failure = new LineToolError(
          'LINE_OCR_TEMP_CLEANUP_FAILED',
          'OCR completed, but its owned temporary image directory could not be removed.',
          { cleanupError: cleanupError?.code || cleanupError?.name || 'unknown' },
        );
      } else if (failure instanceof LineToolError) {
        failure.details = { ...failure.details, temporaryCleanupFailed: true };
      }
    }
  }

  if (failure) throw failure;
  return result;
}

/**
 * Fingerprint an explicit source-pixel rectangle of an already-captured PNG.
 * It neither captures a screen nor interacts with LINE. The optional returned
 * image is the cropped region only, never the full source PNG.
 *
 * @param {{type?: string, data?: string, mimeType?: string, image_url?: string}} imageContent
 * @param {{x: number, y: number, width: number, height: number}} region
 * @param {{includeImage?: boolean}} [options]
 * @returns {Promise<{sha256: string, width: number, height: number, region: {x: number, y: number, width: number, height: number}, image?: {type: 'image', data: string, mimeType: 'image/png'}}>}
 */
export async function fingerprintLineRegion(imageContent, region, options = {}) {
  if (process.platform !== 'win32') {
    throw new LineToolError('LINE_OCR_RUNTIME_UNAVAILABLE', 'Local LINE image fingerprinting requires Windows PowerShell 5.1 and System.Drawing.');
  }

  const fingerprintOptions = normalizeFingerprintOptions(options);
  const config = normalizeOptions({});
  const png = decodePngImageContent(imageContent, config.maxBytes);
  const normalizedRegion = normalizeFingerprintRegion(region, readPngDimensions(png));
  let temporaryDirectory;
  let result;
  let failure;

  try {
    temporaryDirectory = await fs.mkdtemp(path.join(os.tmpdir(), 'line-fingerprint-'));
    const inputPath = path.join(temporaryDirectory, 'input.png');
    await fs.writeFile(inputPath, png, { flag: 'wx' });

    const response = await invokeFingerprint(inputPath, normalizedRegion, config, fingerprintOptions);
    result = normalizeFingerprintResult(response, normalizedRegion, fingerprintOptions);
  } catch (error) {
    failure = error instanceof LineToolError
      ? error
      : new LineToolError(
        'LINE_OCR_TEMP_UNAVAILABLE',
        'Local image fingerprinting could not create or write its owned temporary PNG input.',
        { failureCode: error?.code || error?.name || 'unknown' },
      );
  }

  if (temporaryDirectory) {
    try {
      // This directory came from mkdtemp above; no caller-supplied path is ever removed.
      await fs.rm(temporaryDirectory, { recursive: true, force: true, maxRetries: 2 });
    } catch (cleanupError) {
      if (!failure) {
        failure = new LineToolError(
          'LINE_OCR_TEMP_CLEANUP_FAILED',
          'Image fingerprinting completed, but its owned temporary image directory could not be removed.',
          { cleanupError: cleanupError?.code || cleanupError?.name || 'unknown' },
        );
      } else if (failure instanceof LineToolError) {
        failure.details = { ...failure.details, temporaryCleanupFailed: true };
      }
    }
  }

  if (failure) throw failure;
  return result;
}

/** Normalize the PowerShell bridge result before exposing screenshot pixels. */
export function normalizeOcrResult(value) {
  if (!value || typeof value !== 'object' || value.success !== true) {
    throw ocrResponseError(value);
  }

  const width = positiveInteger(value.width, 'OCR image width');
  const height = positiveInteger(value.height, 'OCR image height');
  const language = requiredText(value.language, 'OCR language');
  const ocrScaleFactor = positiveInteger(value.ocrScaleFactor ?? 1, 'OCR scale factor');
  if (!Array.isArray(value.lines)) {
    throw new LineToolError('LINE_OCR_PROTOCOL_ERROR', 'Windows OCR did not return a lines array.');
  }

  return {
    width,
    height,
    language,
    coordinateSpace: 'input-png-pixels',
    scaleFactor: 1,
    ocrScaleFactor,
    lines: value.lines.map((line, index) => normalizeLine(line, index, width, height)),
  };
}

function normalizeFingerprintResult(value, expectedRegion, options) {
  if (!value || typeof value !== 'object' || value.success !== true) {
    throw ocrResponseError(value);
  }

  const sha256 = typeof value.sha256 === 'string' && /^[0-9a-f]{64}$/iu.test(value.sha256)
    ? value.sha256.toLowerCase()
    : null;
  if (!sha256) {
    throw new LineToolError('LINE_OCR_PROTOCOL_ERROR', 'The local image fingerprint bridge did not return a SHA-256 digest.');
  }
  const width = positiveInteger(value.width, 'fingerprint width');
  const height = positiveInteger(value.height, 'fingerprint height');
  let region;
  try {
    region = normalizeFingerprintRegion(value.region);
  } catch {
    throw new LineToolError('LINE_OCR_PROTOCOL_ERROR', 'The local image fingerprint bridge returned an invalid source-pixel region.');
  }
  if (!sameRegion(region, expectedRegion) || width !== expectedRegion.width || height !== expectedRegion.height) {
    throw new LineToolError('LINE_OCR_PROTOCOL_ERROR', 'The local image fingerprint bridge changed the requested source-pixel region.');
  }

  const result = { sha256, width, height, region };
  if (options.includeImage) {
    if (typeof value.imageData !== 'string') {
      throw new LineToolError('LINE_OCR_PROTOCOL_ERROR', 'The local image fingerprint bridge did not return the requested cropped PNG.');
    }
    let croppedPng;
    let dimensions;
    try {
      croppedPng = decodePngImageContent({ type: 'image', mimeType: 'image/png', data: value.imageData }, FINGERPRINT_IMAGE_MAX_BYTES);
      dimensions = readPngDimensions(croppedPng);
    } catch {
      throw new LineToolError('LINE_OCR_PROTOCOL_ERROR', 'The local image fingerprint bridge returned an invalid cropped PNG.');
    }
    if (dimensions.width !== width || dimensions.height !== height) {
      throw new LineToolError('LINE_OCR_PROTOCOL_ERROR', 'The local image fingerprint bridge returned a cropped PNG with unexpected dimensions.');
    }
    result.image = { type: 'image', data: croppedPng.toString('base64'), mimeType: 'image/png' };
  } else if (value.imageData !== undefined) {
    throw new LineToolError('LINE_OCR_PROTOCOL_ERROR', 'The local image fingerprint bridge returned a cropped PNG that was not requested.');
  }
  return result;
}

/**
 * Canonical form used only for exact label comparison: NFC plus collapsed
 * whitespace. It preserves case, punctuation, Han characters, and emoji.
 */
export function canonicalOcrText(value) {
  if (typeof value !== 'string') throw new TypeError('OCR label text must be a string.');
  return value.normalize('NFC')
    .replace(/[\s\u00a0]+/gu, ' ')
    .trim()
    // Windows OCR often emits a delimiter between adjacent Han glyphs. This
    // is a deterministic formatting normalization, not a fuzzy text match.
    .replace(/(\p{Script=Han}) (?=\p{Script=Han})/gu, '$1');
}

/**
 * Return a recognized exact label, or null. There is deliberately no fuzzy
 * search, case folding, coordinate prediction, or click action. A multiline
 * label may span consecutive OCR lines; its rectangle is the union of those
 * reported line rectangles.
 *
 * @param {{lines?: Array<{text?: string, x?: number, y?: number, width?: number, height?: number}>}} ocr
 * @param {string|string[]} labels
 */
export function findOcrLabel(ocr, labels) {
  return collectOcrLabelMatches(ocr, labels, 1)[0] ?? null;
}

/**
 * Find one exact label only when it appears once. Use this before treating OCR
 * geometry as a semantic target: repeated visible labels are ambiguous and
 * must not be resolved by position alone.
 */
export function findUniqueOcrLabel(ocr, labels) {
  const matches = collectOcrLabelMatches(ocr, labels);
  if (matches.length > 1) {
    throw new LineToolError(
      'LINE_OCR_LABEL_AMBIGUOUS',
      `OCR found ${matches.length} exact label matches; choose a unique label instead of guessing by position.`,
      { matchCount: matches.length },
    );
  }
  return matches[0] ?? null;
}

function collectOcrLabelMatches(ocr, labels, limit = Infinity) {
  if (!ocr || !Array.isArray(ocr.lines)) throw new TypeError('ocr must contain a lines array.');
  const requested = Array.isArray(labels) ? labels : [labels];
  if (requested.length === 0) throw new TypeError('labels must contain at least one exact label.');

  const targets = new Map();
  for (const label of requested) {
    const canonical = canonicalOcrText(label);
    if (!canonical) throw new TypeError('labels must not contain an empty label.');
    if (!targets.has(canonical)) targets.set(canonical, label);
  }

  const matches = [];
  for (let first = 0; first < ocr.lines.length; first += 1) {
    let text = '';
    const matchedLines = [];
    for (let last = first; last < ocr.lines.length; last += 1) {
      const line = ocr.lines[last];
      if (!line || typeof line.text !== 'string') break;
      text = text ? `${text}\n${line.text}` : line.text;
      matchedLines.push(line);
      const canonical = canonicalOcrText(text);
      const label = targets.get(canonical);
      if (label !== undefined) {
        const bounds = unionBounds(matchedLines);
        if (!bounds) throw new TypeError('matched OCR lines must contain finite geometry.');
        matches.push({
          label,
          text,
          lines: [...matchedLines],
          x: bounds.x,
          y: bounds.y,
          width: bounds.width,
          height: bounds.height,
        });
        if (matches.length >= limit) return matches;
      }
    }
  }

  return matches;
}

async function invokeOcr(inputPath, config) {
  return invokeOcrBridge(inputPath, config, ['-PaddingPixels', String(config.paddingPixels)]);
}

async function invokeFingerprint(inputPath, region, config, options) {
  const extraArgs = [
    '-FingerprintRegion',
    '-RegionX', String(region.x),
    '-RegionY', String(region.y),
    '-RegionWidth', String(region.width),
    '-RegionHeight', String(region.height),
  ];
  if (options.includeImage) {
    extraArgs.push('-IncludeImage', '-MaxReturnedImageBytes', String(FINGERPRINT_IMAGE_MAX_BYTES));
  }
  return invokeOcrBridge(inputPath, config, extraArgs);
}

async function invokeOcrBridge(inputPath, config, extraArgs = []) {
  const args = [
    '-NoLogo',
    '-NoProfile',
    '-NonInteractive',
    '-ExecutionPolicy', 'Bypass',
    '-File', OCR_SCRIPT,
    '-InputPath', inputPath,
    '-MaxBytes', String(config.maxBytes),
    '-MaxDimension', String(config.maxDimension),
    '-UpscaleFactor', String(config.upscaleFactor),
    '-PreferredLanguages', config.preferredLanguages.join(','),
    ...extraArgs,
  ];

  let stdout;
  try {
    ({ stdout } = await execFile(WINDOWS_POWERSHELL, args, {
      windowsHide: true,
      timeout: config.timeoutMs,
      maxBuffer: 2 * 1024 * 1024,
      encoding: 'utf8',
    }));
  } catch (error) {
    const response = parseBridgeResponse(error?.stdout, { optional: true });
    if (response) return response;
    if (error?.killed || error?.signal === 'SIGTERM') {
      throw new LineToolError('LINE_OCR_TIMEOUT', `Local OCR exceeded its ${config.timeoutMs} ms timeout.`);
    }
    if (error?.code === 'ERR_CHILD_PROCESS_STDIO_MAXBUFFER') {
      throw new LineToolError('LINE_OCR_OUTPUT_TOO_LARGE', 'The local OCR bridge returned more text geometry than the bounded adapter accepts.');
    }
    if (error?.code === 'ENOENT') {
      throw new LineToolError('LINE_OCR_RUNTIME_UNAVAILABLE', 'Windows PowerShell 5.1 was not found for local OCR.', bridgeProcessDetails(error));
    }
    if (typeof error?.code === 'string') {
      throw new LineToolError('LINE_OCR_RUNTIME_UNAVAILABLE', 'Windows PowerShell could not start the local OCR bridge.', bridgeProcessDetails(error));
    }
    throw new LineToolError('LINE_OCR_BRIDGE_FAILED', 'Windows PowerShell exited without a structured OCR response.', bridgeProcessDetails(error));
  }
  return parseBridgeResponse(stdout);
}

function bridgeProcessDetails(error) {
  const details = {
    processErrorCode: primitiveErrorField(error?.code),
    processErrno: primitiveErrorField(error?.errno),
    processSignal: typeof error?.signal === 'string' ? error.signal : null,
    processKilled: error?.killed === true,
    processSyscall: typeof error?.syscall === 'string' ? error.syscall : null,
    processErrorName: typeof error?.name === 'string' ? error.name : null,
  };
  const stderr = typeof error?.stderr === 'string'
    ? error.stderr
    : Buffer.isBuffer(error?.stderr) ? error.stderr.toString('utf8') : '';
  details.stderrBytes = Buffer.byteLength(stderr, 'utf8');
  details.stderrPresent = details.stderrBytes > 0;
  return details;
}

function primitiveErrorField(value) {
  return typeof value === 'string' || typeof value === 'number' ? value : null;
}

function parseBridgeResponse(stdout, { optional = false } = {}) {
  const text = typeof stdout === 'string' ? stdout.trim() : '';
  if (!text) {
    if (optional) return null;
    throw new LineToolError('LINE_OCR_PROTOCOL_ERROR', 'The local OCR bridge returned no structured result.');
  }
  try {
    return JSON.parse(text);
  } catch {
    if (optional) return null;
    throw new LineToolError('LINE_OCR_PROTOCOL_ERROR', 'The local OCR bridge returned an invalid structured result.');
  }
}

function normalizeFingerprintOptions(options) {
  if (!options || typeof options !== 'object' || Array.isArray(options)) {
    throw new LineToolError('LINE_OCR_INVALID_ARGUMENT', 'Image fingerprint options must be an object.');
  }
  if (options.includeImage !== undefined && typeof options.includeImage !== 'boolean') {
    throw new LineToolError('LINE_OCR_INVALID_ARGUMENT', 'includeImage must be a boolean when supplied.');
  }
  return { includeImage: options.includeImage === true };
}

function normalizeOptions(options) {
  if (!options || typeof options !== 'object' || Array.isArray(options)) {
    throw new LineToolError('LINE_OCR_INVALID_ARGUMENT', 'OCR options must be an object.');
  }
  const maxBytes = boundedInteger(options.maxBytes ?? LINE_OCR_DEFAULTS.maxBytes, 'maxBytes', 1, LINE_OCR_DEFAULTS.maxBytes);
  const maxDimension = boundedInteger(options.maxDimension ?? LINE_OCR_DEFAULTS.maxDimension, 'maxDimension', 1, LINE_OCR_DEFAULTS.maxDimension);
  const timeoutMs = boundedInteger(options.timeoutMs ?? LINE_OCR_DEFAULTS.timeoutMs, 'timeoutMs', 1_000, 60_000);
  const upscaleFactor = boundedInteger(options.upscaleFactor ?? LINE_OCR_DEFAULTS.upscaleFactor, 'upscaleFactor', 1, 4);
  const paddingPixels = boundedInteger(options.paddingPixels ?? 0, 'paddingPixels', 0, 32);
  const preferredLanguages = options.preferredLanguages ?? LINE_OCR_DEFAULTS.preferredLanguages;
  if (!Array.isArray(preferredLanguages) || preferredLanguages.length === 0) {
    throw new LineToolError('LINE_OCR_INVALID_ARGUMENT', 'preferredLanguages must be a nonempty array containing zh-Hant and/or en.');
  }
  const normalizedLanguages = [...new Set(preferredLanguages.map((language) => {
    if (!['zh-Hant', 'en'].includes(language)) {
      throw new LineToolError('LINE_OCR_INVALID_ARGUMENT', 'preferredLanguages may contain only zh-Hant and en.');
    }
    return language;
  }))];

  return { maxBytes, maxDimension, timeoutMs, upscaleFactor, paddingPixels, preferredLanguages: normalizedLanguages };
}

function decodePngImageContent(imageContent, maxBytes) {
  if (!imageContent || typeof imageContent !== 'object' || Array.isArray(imageContent)) {
    throw new LineToolError('LINE_OCR_INVALID_IMAGE', 'imageContent must be an image content object containing PNG data.');
  }
  if (imageContent.type !== undefined && imageContent.type !== 'image') {
    throw new LineToolError('LINE_OCR_INVALID_IMAGE', 'imageContent must describe an image.');
  }

  let mimeType = imageContent.mimeType;
  let data = imageContent.data;
  if (typeof data !== 'string' && typeof imageContent.image_url === 'string') data = imageContent.image_url;
  if (typeof data !== 'string') {
    throw new LineToolError('LINE_OCR_INVALID_IMAGE', 'imageContent must contain base64 PNG data.');
  }

  const dataUri = /^data:([^;,]+);base64,([\s\S]*)$/iu.exec(data);
  if (dataUri) {
    mimeType ??= dataUri[1];
    data = dataUri[2];
  }
  if (String(mimeType || '').toLowerCase() !== 'image/png') {
    throw new LineToolError('LINE_OCR_INVALID_IMAGE', 'Local LINE OCR accepts PNG image content only.');
  }

  const compact = data.replace(/\s+/gu, '');
  if (!compact || !/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/u.test(compact)) {
    throw new LineToolError('LINE_OCR_INVALID_IMAGE', 'PNG image data must be valid base64.');
  }
  const png = Buffer.from(compact, 'base64');
  if (png.length > maxBytes) {
    throw new LineToolError('LINE_OCR_IMAGE_TOO_LARGE', `PNG image data exceeds the ${maxBytes}-byte OCR limit.`);
  }
  if (png.length < PNG_SIGNATURE.length || !png.subarray(0, PNG_SIGNATURE.length).equals(PNG_SIGNATURE)) {
    throw new LineToolError('LINE_OCR_INVALID_IMAGE', 'imageContent did not contain a PNG file.');
  }
  return png;
}

function readPngDimensions(png) {
  if (!Buffer.isBuffer(png) || png.length < 33 || png.readUInt32BE(8) !== 13 || png.subarray(12, 16).toString('ascii') !== 'IHDR') {
    throw new LineToolError('LINE_OCR_INVALID_IMAGE', 'imageContent did not contain a PNG IHDR header.');
  }
  const width = png.readUInt32BE(16);
  const height = png.readUInt32BE(20);
  if (width < 1 || height < 1) {
    throw new LineToolError('LINE_OCR_INVALID_IMAGE', 'PNG image dimensions must be positive.');
  }
  return { width, height };
}

function normalizeFingerprintRegion(region, dimensions = null) {
  if (!region || typeof region !== 'object' || Array.isArray(region)) {
    throw new LineToolError('LINE_OCR_INVALID_REGION', 'Fingerprint region must be an object with source PNG pixel coordinates.');
  }
  const x = nonnegativeSafeInteger(region.x, 'region.x');
  const y = nonnegativeSafeInteger(region.y, 'region.y');
  const width = positiveSafeInteger(region.width, 'region.width');
  const height = positiveSafeInteger(region.height, 'region.height');
  if (dimensions && (x + width > dimensions.width || y + height > dimensions.height)) {
    throw new LineToolError('LINE_OCR_INVALID_REGION', 'Fingerprint region must be wholly inside the input PNG.');
  }
  return { x, y, width, height };
}

function nonnegativeSafeInteger(value, name) {
  if (typeof value !== 'number' || !Number.isSafeInteger(value) || value < 0) {
    throw new LineToolError('LINE_OCR_INVALID_REGION', `${name} must be a nonnegative safe integer.`);
  }
  return value;
}

function positiveSafeInteger(value, name) {
  if (typeof value !== 'number' || !Number.isSafeInteger(value) || value < 1) {
    throw new LineToolError('LINE_OCR_INVALID_REGION', `${name} must be a positive safe integer.`);
  }
  return value;
}

function sameRegion(left, right) {
  return left.x === right.x && left.y === right.y && left.width === right.width && left.height === right.height;
}

function normalizeLine(value, index, imageWidth, imageHeight) {
  if (!value || typeof value !== 'object') {
    throw new LineToolError('LINE_OCR_PROTOCOL_ERROR', `OCR line ${index} was invalid.`);
  }
  const text = typeof value.text === 'string' ? value.text : '';
  if (!Array.isArray(value.words)) {
    throw new LineToolError('LINE_OCR_PROTOCOL_ERROR', `OCR line ${index} did not include words.`);
  }
  const words = value.words.map((word, wordIndex) => normalizeWord(word, index, wordIndex, imageWidth, imageHeight));
  const geometry = hasGeometry(value)
    ? normalizeGeometry(value, `OCR line ${index}`, imageWidth, imageHeight)
    : geometryFromWords(words, `OCR line ${index}`);
  return { text, words, ...geometry };
}

function normalizeWord(value, lineIndex, wordIndex, imageWidth, imageHeight) {
  if (!value || typeof value !== 'object') {
    throw new LineToolError('LINE_OCR_PROTOCOL_ERROR', `OCR word ${lineIndex}:${wordIndex} was invalid.`);
  }
  return {
    text: typeof value.text === 'string' ? value.text : '',
    ...normalizeGeometry(value, `OCR word ${lineIndex}:${wordIndex}`, imageWidth, imageHeight),
  };
}

function normalizeGeometry(value, name, imageWidth, imageHeight) {
  const x = finiteCoordinate(value.x, `${name} x`);
  const y = finiteCoordinate(value.y, `${name} y`);
  const width = finiteCoordinate(value.width, `${name} width`);
  const height = finiteCoordinate(value.height, `${name} height`);
  if (x < 0 || y < 0 || width < 0 || height < 0 || x + width > imageWidth + 0.001 || y + height > imageHeight + 0.001) {
    throw new LineToolError('LINE_OCR_PROTOCOL_ERROR', `${name} geometry was outside the OCR image bounds.`);
  }
  return { x, y, width, height };
}

function geometryFromWords(words, name) {
  if (words.length === 0) {
    throw new LineToolError('LINE_OCR_PROTOCOL_ERROR', `${name} had neither geometry nor words.`);
  }
  const bounds = unionBounds(words);
  if (!bounds) throw new LineToolError('LINE_OCR_PROTOCOL_ERROR', `${name} word geometry was invalid.`);
  return bounds;
}

function unionBounds(items) {
  if (!Array.isArray(items) || items.length === 0) return null;
  let left = Infinity;
  let top = Infinity;
  let right = -Infinity;
  let bottom = -Infinity;
  for (const item of items) {
    const x = Number(item?.x);
    const y = Number(item?.y);
    const width = Number(item?.width);
    const height = Number(item?.height);
    if (![x, y, width, height].every(Number.isFinite) || width < 0 || height < 0) return null;
    left = Math.min(left, x);
    top = Math.min(top, y);
    right = Math.max(right, x + width);
    bottom = Math.max(bottom, y + height);
  }
  return { x: left, y: top, width: right - left, height: bottom - top };
}

function hasGeometry(value) {
  return ['x', 'y', 'width', 'height'].every((key) => value[key] !== undefined);
}

function positiveInteger(value, name) {
  const number = Number(value);
  if (!Number.isInteger(number) || number < 1) {
    throw new LineToolError('LINE_OCR_PROTOCOL_ERROR', `${name} must be a positive integer.`);
  }
  return number;
}

function boundedInteger(value, name, minimum, maximum) {
  const number = Number(value);
  if (!Number.isInteger(number) || number < minimum || number > maximum) {
    throw new LineToolError('LINE_OCR_INVALID_ARGUMENT', `${name} must be an integer from ${minimum} to ${maximum}.`);
  }
  return number;
}

function finiteCoordinate(value, name) {
  const number = Number(value);
  if (!Number.isFinite(number)) {
    throw new LineToolError('LINE_OCR_PROTOCOL_ERROR', `${name} must be finite.`);
  }
  return number;
}

function requiredText(value, name) {
  if (typeof value !== 'string' || !value.trim()) {
    throw new LineToolError('LINE_OCR_PROTOCOL_ERROR', `${name} must be a nonempty string.`);
  }
  return value;
}

function ocrResponseError(value) {
  const code = typeof value?.code === 'string' ? value.code : 'LINE_OCR_PROTOCOL_ERROR';
  const message = typeof value?.message === 'string' && value.message
    ? value.message
    : 'The local OCR bridge did not return a successful result.';
  const details = {};
  if (Array.isArray(value?.availableLanguages)) details.availableLanguages = value.availableLanguages.filter((language) => typeof language === 'string');
  if (Array.isArray(value?.requestedLanguages)) details.requestedLanguages = value.requestedLanguages.filter((language) => typeof language === 'string');
  if (typeof value?.failureType === 'string') details.failureType = value.failureType;
  return new LineToolError(code, message, details);
}
