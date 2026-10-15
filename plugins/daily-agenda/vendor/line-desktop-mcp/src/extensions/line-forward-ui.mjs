import { createHash, randomBytes } from 'node:crypto';

import { elementTarget, snapshot } from './cua-line-client.mjs';
import { LineToolError } from './line-runtime.mjs';
import { runUiInput, resolveOpenedMenu, menuItemTarget, replySourceVisualView } from './line-ui.mjs';
import { canonicalOcrText, findUniqueOcrLabel } from './line-ocr.mjs';

const SESSION_MS = 120_000;
const SHARE_LABELS = new Set(['分享', 'Share', '轉傳', 'Forward']);
const SEARCH_LABELS = new Set(['搜尋', 'Search', '搜尋好友', 'Search friends']);
const KINDS = new Set(['direct', 'group']);
const record = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const hash = value => createHash('sha256').update(JSON.stringify(value)).digest('hex');
const fail = (code, message, details = {}) => { throw new LineToolError(code, message, { sendDispatched: false, ...details }); };
const label = item => item?.label ?? item?.name ?? item?.text ?? '';
const role = item => String(item?.role ?? '').toLowerCase();
const marker = item => `${role(item)} ${String(item?.semantic_role ?? '').toLowerCase()}`;
const sameTarget = (a, b) => a?.pid === b?.pid && a?.window_id === b?.window_id;
const images = state => Array.isArray(state?.images) ? state.images.filter(item => item?.type === 'image') : [];

function bindingDigest(binding) {
  const source = binding?.source;
  const recipient = binding?.recipient;
  if (!record(source) || !record(recipient)
    || !/^chat:[0-9a-f]{24}$/u.test(source.chatRef ?? '')
    || !/^chat:[0-9a-f]{24}$/u.test(recipient.chatRef ?? '')
    || !/^message:[0-9a-f]{24}$/u.test(source.sourceRef ?? '')
    || !/^[0-9a-f]{64}$/u.test(source.digest ?? '')
    || !Number.isSafeInteger(source.sourceTimestamp)
    || ![0, 1, 2, 3, 14].includes(source.contentType)
    || typeof source.date !== 'string' || typeof source.time !== 'string'
    || !KINDS.has(source.chatType) || !KINDS.has(recipient.chatType)
    || typeof source.chatName !== 'string' || !source.chatName
    || typeof recipient.chatName !== 'string' || !recipient.chatName
    || typeof binding.ownSenderRef !== 'string') {
    fail('LINE_FORWARD_BINDING_INVALID', 'Forwarding needs an exact local source and recipient binding.');
  }
  return hash({ source, recipient, ownSenderRef: binding.ownSenderRef });
}

async function assertRecipientIdentity(ui, recipient) {
  if (typeof ui.readChatIdentity !== 'function') {
    fail('LINE_FORWARD_RECIPIENT_IDENTITY_UNVERIFIED', 'The local GUI chat-identity reader is unavailable.');
  }
  const result = await ui.readChatIdentity({ chatName: recipient.chatName });
  if (result?.chatName !== recipient.chatName || result.chatRef !== recipient.chatRef
    || result.chatIdentity?.kind !== recipient.chatType
    || result.chatIdentity?.displayName !== recipient.chatName
    || result.chatIdentity?.guiDisplayNameUnique !== true
    || result.chatIdentity?.uiIdentityVerified !== false
    || result.scope?.kind !== 'local_gui_chat_identity'
    || result.count !== 0 || !Array.isArray(result.messages) || result.messages.length !== 0) {
    fail('LINE_FORWARD_RECIPIENT_IDENTITY_UNVERIFIED',
      'The recipient name, type and chat reference are no longer unique in the local GUI identity.');
  }
}

function one(items, code, description) {
  if (items.length !== 1) fail(code, `${description}: expected exactly one, found ${items.length}.`, { candidateCount: items.length });
  return items[0];
}

function sourceElement(state, source) {
  // The local sourceRef is not a LINE UI identifier. Require the visible
  // content and a separate date/time or timestamp on one accessible bubble.
  // A plain filename/suffix or a text match elsewhere in the chat is unsafe.
  const candidates = state.elements.filter(item => {
    if (!/message|bubble/u.test(marker(item))) return false;
    const timeMatches = (Number.isSafeInteger(item.sourceTimestamp)
      && item.sourceTimestamp === source.sourceTimestamp)
      || (item.date === source.date && item.time === source.time);
    if (!timeMatches || item.contentType !== source.contentType
      || typeof source.sender !== 'string' || !source.sender
      || item.sender !== source.sender) return false;
    if (source.contentType === 0) return typeof source.text === 'string'
      && source.text.length > 0 && label(item) === source.text;
    const attachment = source.attachment;
    return record(attachment) && typeof attachment.fileName === 'string'
      && attachment.fileName.length > 0 && item.fileName === attachment.fileName
      && Number.isSafeInteger(attachment.declaredFileBytes)
      && item.declaredFileBytes === attachment.declaredFileBytes;
  });
  return one(candidates, 'LINE_FORWARD_SOURCE_UI_UNVERIFIED', 'Exact visible source bubble');
}

function inside(item, bounds) {
  return item.x >= bounds.x && item.y >= bounds.y
    && item.x + item.width <= bounds.x + bounds.width
    && item.y + item.height <= bounds.y + bounds.height;
}

function exactOcr(ocr, labels, description) {
  let found;
  try { found = findUniqueOcrLabel(ocr, labels); } catch { /* ambiguous */ }
  if (!found) fail('LINE_FORWARD_SOURCE_UI_UNVERIFIED', `${description} was not unique in the fresh LINE screenshot.`);
  return found;
}

function visibleTimeLabels(time) {
  const match = /^(\d{2}):(\d{2})/u.exec(time ?? '');
  if (!match) return [];
  const hour = Number(match[1]);
  const minute = match[2];
  const period = hour < 12 ? '上午' : '下午';
  const twelveHour = hour % 12 || 12;
  return [`${period} ${twelveHour}:${minute}`, `${period}${twelveHour}:${minute}`,
    `${match[1]}:${minute}`];
}

function outgoingTimestamp(ocr, labels, content, bounds) {
  const compact = value => canonicalOcrText(value).replace(/\s+/gu, '');
  const canonical = new Set(labels.map(compact));
  return one(ocr.lines.filter(line => canonical.has(compact(line.text ?? ''))
    && inside(line, bounds) && line.x + line.width <= content.x + 8
    && Math.abs((line.y + line.height / 2)
      - (content.y + content.height / 2)) <= 90),
  'LINE_FORWARD_SOURCE_UI_UNVERIFIED', 'Outgoing timestamp beside the source card');
}

function hasVerifiedLatestTail(binding, source) {
  const anchors = binding.uiAnchors?.messages;
  if (binding.uiAnchors?.latestSourceWindowComplete !== true || !Array.isArray(anchors)
    || anchors.length < 1 || anchors.length > 3
    || anchors.some(item => !record(item) || !Number.isSafeInteger(item.sourceTimestamp)
      || typeof item.sourceRef !== 'string')
    || anchors.some((item, index) => index > 0
      && item.sourceTimestamp <= anchors[index - 1].sourceTimestamp)) return false;
  const index = anchors.findIndex(item => item.sourceRef === source.sourceRef
    && item.senderRef === source.senderRef && item.date === source.date
    && item.time === source.time && item.sourceTimestamp === source.sourceTimestamp
    && item.contentType === source.contentType
    && JSON.stringify(item.attachment) === JSON.stringify(source.attachment));
  return index === anchors.length - 1
    || (index === anchors.length - 2 && anchors[index + 1].contentType === 1);
}

function screenshotFrame(item, view) {
  const frame = item?.frame;
  const x = Number(frame?.x);
  const y = Number(frame?.y);
  const width = Number(frame?.w ?? frame?.width);
  const height = Number(frame?.h ?? frame?.height);
  if (![x, y, width, height].every(Number.isFinite) || width <= 0 || height <= 0) return null;
  const root = view.rootFrame;
  if (x < root.x || y < root.y || x + width > root.x + root.width
    || y + height > root.y + root.height) return null;
  const sx = view.dimensions.width / root.width;
  const sy = view.dimensions.height / root.height;
  return { x: Math.floor((x - root.x) * sx), y: Math.floor((y - root.y) * sy),
    width: Math.ceil(width * sx), height: Math.ceil(height * sy) };
}

function messageRows(state, view) {
  return state.elements.filter(item => /^(listitem|list-item)$/u.test(role(item)))
    .map(item => ({ item, frame: screenshotFrame(item, view) }))
    .filter(row => row.frame && inside(row.frame, view.messageBounds))
    .sort((a, b) => a.frame.y - b.frame.y);
}

function inlineShareWords(ocr, row, cardBottom, messageBounds) {
  const matches = [];
  for (const line of ocr.lines) {
    if (!inside(line, row.frame) || line.y < cardBottom + 4) continue;
    const words = Array.isArray(line.words) ? line.words : [];
    for (let index = 1; index < words.length - 2; index += 1) {
      const first = words[index], second = words[index + 1];
      if (first.text !== '分' || second.text !== '享'
        || !['|', '｜'].includes(words[index - 1].text)
        || !['|', '｜'].includes(words[index + 2].text)) continue;
      const bounds = { x: first.x, y: Math.min(first.y, second.y),
        width: second.x + second.width - first.x,
        height: Math.max(first.y + first.height, second.y + second.height)
          - Math.min(first.y, second.y) };
      if (second.x - (first.x + first.width) > 4
        || !inside(bounds, row.frame) || !inside(bounds, messageBounds)
        || bounds.x < messageBounds.x + messageBounds.width / 2) continue;
      matches.push(bounds);
    }
  }
  return matches.length === 1 ? { bounds: matches[0], pixel: center(matches[0]) } : null;
}

function assertOutgoingLatestTailGeometry(state, view, ocr, source, binding, parts, content, time) {
  const bounds = view.messageBounds;
  const contentCenter = content.x + content.width / 2;
  if (contentCenter <= bounds.x + bounds.width / 2
    || time.x + time.width > content.x + 8
    || Math.abs((time.y + time.height / 2) - (content.y + content.height / 2)) > 90) {
    fail('LINE_FORWARD_SOURCE_UI_UNVERIFIED', 'The exact file and timestamp are not one right-aligned outgoing card.');
  }
  const anchors = binding.uiAnchors.messages;
  const index = anchors.findIndex(item => item.sourceRef === source.sourceRef);
  const rows = messageRows(state, view);
  const containing = label => rows.filter(row => inside(label, row.frame));
  const sourceRow = one(containing(content).filter(row => parts.every(part => inside(part, row.frame))),
    'LINE_FORWARD_SOURCE_UI_UNVERIFIED', 'Bound source message row');
  const sourcePosition = rows.indexOf(sourceRow);
  if (sourcePosition < 0 || rows.some((row, position) => position > 0
    && row.frame.y < rows[position - 1].frame.y + rows[position - 1].frame.height - 2)) {
    fail('LINE_FORWARD_SOURCE_UI_UNVERIFIED', 'Visible message rows overlap or are unordered.');
  }
  if (index === anchors.length - 1) {
    if (sourcePosition !== rows.length - 1
      || bounds.y + bounds.height - (sourceRow.frame.y + sourceRow.frame.height) > 12) {
      fail('LINE_FORWARD_SOURCE_UI_UNVERIFIED', 'The file card is not the visible bottom message above the composer.');
    }
    return;
  }
  const next = anchors[index + 1];
  const nextTime = one(ocr.lines.filter(line => visibleTimeLabels(next.time)
    .some(value => canonicalOcrText(value).replace(/\s+/gu, '')
      === canonicalOcrText(line.text ?? '').replace(/\s+/gu, ''))),
  'LINE_FORWARD_SOURCE_UI_UNVERIFIED', 'Next tail image time');
  if (!inside(nextTime, bounds) || nextTime.y <= sourceRow.frame.y + sourceRow.frame.height) {
    fail('LINE_FORWARD_SOURCE_UI_UNVERIFIED', 'The next local image time is not below the selected file.');
  }
  const nextRow = one(containing(nextTime), 'LINE_FORWARD_SOURCE_UI_UNVERIFIED', 'Next tail message row');
  if (sourcePosition !== rows.length - 2 || rows[sourcePosition + 1] !== nextRow
    || bounds.y + bounds.height - (nextRow.frame.y + nextRow.frame.height) > 12) {
    fail('LINE_FORWARD_SOURCE_UI_UNVERIFIED', 'The next image row is not the final visible message above the composer.');
  }
}

async function sourceTarget(state, binding, inspected, ui) {
  const source = binding.source;
  try { return { element: sourceElement(state, source), proofKind: 'exact-accessible-message-metadata' }; }
  catch (error) {
    if (error?.details?.candidateCount !== 0 || ![0, 14].includes(source.contentType)) throw error;
  }
  // A text bubble or file card can be custom drawn. OCR is admissible only when several
  // independent exact labels from the bound local record cluster in one
  // verified message area; a filename or text alone is never a source identity.
  const attachment = source.attachment;
  if ((source.contentType === 14 && (!record(attachment) || typeof attachment.fileName !== 'string'
    || !attachment.fileName || !Number.isSafeInteger(attachment.declaredFileBytes)
    || attachment.declaredFileBytes <= 0)) || (source.contentType === 0
    && (typeof source.text !== 'string' || !source.text))
    || typeof source.date !== 'string' || typeof source.time !== 'string') {
    fail('LINE_FORWARD_SOURCE_UI_UNVERIFIED', 'The bound source lacks exact visible content, sender, date or time evidence.');
  }
  const view = replySourceVisualView(state, inspected.window, inspected.guard, ui.visual);
  if (!view || typeof ui.ocr?.recognizeImage !== 'function') {
    fail('LINE_FORWARD_SOURCE_UI_UNVERIFIED', 'The verified LINE message screenshot is unavailable for file-card OCR.');
  }
  const ocr = await ui.ocr.recognizeImage(view.image);
  if (ocr?.coordinateSpace !== 'input-png-pixels' || ocr.scaleFactor !== 1
    || ocr.width !== view.dimensions.width || ocr.height !== view.dimensions.height) {
    fail('LINE_FORWARD_SOURCE_UI_UNVERIFIED', 'File-card OCR did not match the captured screenshot geometry.');
  }
  const content = exactOcr(ocr, source.contentType === 0 ? source.text : attachment.fileName,
    source.contentType === 0 ? 'Full source text' : 'Full filename');
  const ownLatestTail = source.contentType === 14
    && source.senderRef === binding.ownSenderRef
    && hasVerifiedLatestTail(binding, source);
  const time = ownLatestTail
    ? outgoingTimestamp(ocr, visibleTimeLabels(source.time), content, view.messageBounds)
    : exactOcr(ocr, visibleTimeLabels(source.time), 'Source time');
  const parts = [content, time];
  let inlineShare = null;
  if (!ownLatestTail) {
    if (typeof source.sender !== 'string' || !source.sender) {
      fail('LINE_FORWARD_SOURCE_UI_UNVERIFIED', 'The non-own source lacks a visible sender identity.');
    }
    parts.push(exactOcr(ocr, source.sender, 'Source sender'));
    parts.push(exactOcr(ocr, source.date, 'Source date'));
  }
  if (source.contentType === 14) {
    const bytes = attachment.declaredFileBytes;
    const sizeLabels = [`${bytes} B`, `${bytes} bytes`, `${bytes}bytes`,
      `${bytes.toLocaleString('en-US')} B`, `${bytes.toLocaleString('en-US')} bytes`,
      `檔案大小 : ${bytes}bytes`, `檔案大小: ${bytes}bytes`,
      `檔案大小：${bytes}bytes`, `大小 : ${bytes}Bytes`];
    if (ownLatestTail) {
      const row = one(messageRows(state, view).filter(item => inside(content, item.frame)
        && inside(time, item.frame)), 'LINE_FORWARD_SOURCE_UI_UNVERIFIED', 'File message row');
      const crop = await ui.visual.fingerprintRegion(view.image, row.frame, { includeImage: true });
      if (!crop?.image || JSON.stringify(crop.region) !== JSON.stringify(row.frame)) {
        fail('LINE_FORWARD_SOURCE_UI_UNVERIFIED', 'The exact file row could not be cropped for byte-size OCR.');
      }
      const rowOcr = await ui.ocr.recognizeImage(crop.image, { upscaleFactor: 4 });
      if (rowOcr?.coordinateSpace !== 'input-png-pixels' || rowOcr.scaleFactor !== 1
        || rowOcr.width !== row.frame.width || rowOcr.height !== row.frame.height) {
        fail('LINE_FORWARD_SOURCE_UI_UNVERIFIED', 'The file-row OCR geometry changed.');
      }
      const localSize = exactOcr(rowOcr, sizeLabels, 'Exact byte size in source row');
      const size = { ...localSize, x: localSize.x + row.frame.x, y: localSize.y + row.frame.y };
      parts.push(size);
      inlineShare = inlineShareWords(ocr, row,
        Math.max(content.y + content.height, size.y + size.height), view.messageBounds);
      if (inlineShare) parts.push(inlineShare.bounds);
    } else {
      parts.push(exactOcr(ocr, sizeLabels, 'Exact byte size'));
    }
  }
  if (parts.some(part => !inside(part, view.messageBounds))
    || Math.max(...parts.map(part => part.y + part.height))
      - Math.min(...parts.map(part => part.y)) > 160) {
    fail('LINE_FORWARD_SOURCE_UI_UNVERIFIED', 'File identity labels did not belong to one bounded visible card.');
  }
  if (ownLatestTail) assertOutgoingLatestTailGeometry(state, view, ocr, source, binding, parts, content, time);
  const left = Math.max(view.messageBounds.x, Math.floor(Math.min(...parts.map(part => part.x)) - 3));
  const top = Math.max(view.messageBounds.y, Math.floor(Math.min(...parts.map(part => part.y)) - 3));
  const right = Math.min(view.messageBounds.x + view.messageBounds.width,
    Math.ceil(Math.max(...parts.map(part => part.x + part.width)) + 3));
  const bottom = Math.min(view.messageBounds.y + view.messageBounds.height,
    Math.ceil(Math.max(...parts.map(part => part.y + part.height)) + 3));
  const region = { x: left, y: top, width: right - left, height: bottom - top };
  const fingerprint = await ui.visual.fingerprintRegion(view.image, region, { includeImage: false });
  if (!/^[0-9a-f]{64}$/u.test(fingerprint?.sha256 ?? '')
    || JSON.stringify(fingerprint.region) !== JSON.stringify(region)) {
    fail('LINE_FORWARD_SOURCE_UI_UNVERIFIED', 'The exact file-card crop could not be fingerprinted.');
  }
  return { pixel: { x: Math.floor(content.x + content.width / 2),
    y: Math.floor(content.y + content.height / 2) },
  ...(inlineShare ? { inlineSharePixel: inlineShare.pixel } : {}), fingerprint,
  proofKind: source.contentType === 14 ? 'fresh-file-card-ocr-and-pixel-fingerprint'
    : 'fresh-text-bubble-ocr-and-pixel-fingerprint' };
}

function semantic(state, predicate, description) {
  return one(state.elements.filter(predicate), 'LINE_FORWARD_UI_UNVERIFIED', description);
}

function searchControl(state) {
  return semantic(state, item => role(item).includes('edit') && SEARCH_LABELS.has(label(item)), 'Recipient search control');
}

function recipientResult(state, recipient) {
  return semantic(state, item => /listitem|list-item|recipient|search-result/u.test(role(item))
    && label(item) === recipient.chatName
    && (item.chatType === undefined || item.chatType === recipient.chatType)
    && item.selected !== true, 'Exact recipient search result');
}

function selectedRecipient(state, recipient) {
  const selected = state.elements.filter(item => item.selected === true
    && /recipient|chip|listitem|list-item/u.test(role(item)));
  const count = state.selectedCount ?? selected.length;
  if (count !== 1 || selected.length !== 1) {
    fail('LINE_FORWARD_RECIPIENT_UNVERIFIED', 'The share dialog must show exactly one selected recipient.');
  }
  const item = selected[0];
  if (label(item) !== recipient.chatName
    || (item.chatType !== undefined && item.chatType !== recipient.chatType)) {
    fail('LINE_FORWARD_RECIPIENT_MISMATCH', 'The selected recipient differs from the locally bound chat.');
  }
  return item;
}

function finalShare(state) {
  return semantic(state, item => /button/u.test(role(item))
    && SHARE_LABELS.has(label(item)) && item.enabled !== false,
  'Final Share button');
}

function reviewFingerprint(state, recipient) {
  const selected = selectedRecipient(state, recipient);
  const button = finalShare(state);
  return hash({ selectedCount: 1, selectedName: label(selected), selectedKind: selected.chatType ?? recipient.chatType,
    selected: selected.selected, buttonName: label(button), buttonEnabled: button.enabled !== false });
}

function dialogProof(state, recipient, { selected = false, windowTitle } = {}) {
  const dialogs = state.elements.filter(item => /dialog|window/u.test(role(item))
    && SHARE_LABELS.has(label(item)));
  if (dialogs.length !== 1) {
    if (dialogs.length !== 0 || !SHARE_LABELS.has(windowTitle)) {
      fail('LINE_FORWARD_UI_UNVERIFIED', 'The Share dialog lacked one exact title or dialog control.');
    }
    searchControl(state);
  }
  if (selected) {
    selectedRecipient(state, recipient);
    finalShare(state);
  }
}

const compactOcr = value => canonicalOcrText(value ?? '').replace(/\s+/gu, '');
function qtFrame(item, state, window, dimensions) {
  const frame = item?.frame;
  const root = state.window_bounds ?? window?.bounds;
  if (!frame || !root || !Number.isFinite(frame.x) || !Number.isFinite(frame.y)) return null;
  const sx = dimensions.width / root.width;
  const sy = dimensions.height / root.height;
  return { x: (frame.x - root.x) * sx, y: (frame.y - root.y) * sy,
    width: frame.w * sx, height: frame.h * sy };
}
function center(item) { return { x: Math.floor(item.x + item.width / 2), y: Math.floor(item.y + item.height / 2) }; }
function boundedOcr(ocr, expected, bounds) {
  return ocr.lines.filter(line => compactOcr(line.text) === compactOcr(expected)
    && (!bounds || inside(line, bounds)));
}
async function qtDialogProof(state, window, recipient, ui, phase = 'open') {
  const image = one(images(state), 'LINE_FORWARD_UI_UNVERIFIED', 'Fresh Qt Share screenshot');
  const dimensions = ui.visual?.imageDimensions?.(image);
  if (!dimensions || dimensions.width < 300 || dimensions.height < 450
    || dimensions.width > 800 || dimensions.height > 900) {
    fail('LINE_FORWARD_UI_UNVERIFIED', 'Qt Share screenshot geometry was unavailable.');
  }
  const ocr = await ui.ocr?.recognizeImage?.(image);
  if (ocr?.coordinateSpace !== 'input-png-pixels' || ocr.scaleFactor !== 1
    || ocr.width !== dimensions.width || ocr.height !== dimensions.height
    || boundedOcr(ocr, '選擇傳送對象').length !== 1) {
    fail('LINE_FORWARD_UI_UNVERIFIED', 'The new LINE window did not have one exact Share-recipient heading.');
  }
  const edit = one(state.elements.filter(item => role(item) === 'edit'
    && qtFrame(item, state, window, dimensions)?.y < 140),
  'LINE_FORWARD_UI_UNVERIFIED', 'Qt Share recipient search');
  if (phase !== 'open' && edit.value !== recipient.chatName) {
    fail('LINE_FORWARD_RECIPIENT_MISMATCH', 'The Share search query changed.');
  }
  const rowItems = state.elements.filter(item => /^(listitem|list-item)$/u.test(role(item))
    && qtFrame(item, state, window, dimensions)?.y > 125);
  const matchedRows = rowItems.filter(item => {
    const frame = qtFrame(item, state, window, dimensions);
    return boundedOcr(ocr, recipient.chatName, frame).length === 1;
  });
  if (phase === 'results') {
    if (matchedRows.length < 1 || matchedRows.length > 2
      || matchedRows.some(item => item.chatType !== undefined && item.chatType !== recipient.chatType)
      || rowItems.some(item => item.selected === true)) {
      fail('LINE_FORWARD_RECIPIENT_UNVERIFIED', 'The exact local recipient could not be isolated in Qt search results.');
    }
    return { edit, result: matchedRows[0] };
  }
  if (phase !== 'selected') return { edit };
  const selectedRows = rowItems.filter(item => item.selected === true);
  if (selectedRows.length !== 1 || matchedRows.length < 1
    || selectedRows[0] !== matchedRows.find(item => item === selectedRows[0])
    || (selectedRows[0].chatType !== undefined && selectedRows[0].chatType !== recipient.chatType)) {
    fail('LINE_FORWARD_RECIPIENT_MISMATCH', 'Exactly one bound recipient was not selected.');
  }
  const viewport = one(state.elements.filter(item => label(item) === 'qt_scrollarea_viewport'
    && qtFrame(item, state, window, dimensions)?.y > dimensions.height * .65),
  'LINE_FORWARD_RECIPIENT_UNVERIFIED', 'One Qt selected-recipient chip area');
  const chip = state.elements.filter(item => role(item) === 'group'
    && item.parent_index === viewport.element_index
    && qtFrame(item, state, window, dimensions)?.width > 50);
  if (chip.length !== 1) fail('LINE_FORWARD_RECIPIENT_UNVERIFIED', 'Qt Share did not show exactly one selected-recipient chip.');
  const footer = one(state.elements.filter(item => role(item) === 'group'
    && qtFrame(item, state, window, dimensions)?.y > dimensions.height * .75
    && qtFrame(item, state, window, dimensions)?.width >= 80
    && qtFrame(item, state, window, dimensions)?.width <= 110
    && Math.abs(qtFrame(item, state, window, dimensions)?.height - 30) < 2
    && qtFrame(item, state, window, dimensions)?.x < dimensions.width / 2),
  'LINE_FORWARD_UI_UNVERIFIED', 'Qt final Share control');
  const footerFrame = qtFrame(footer, state, window, dimensions);
  const finalLabel = one(ocr.lines.filter(line => compactOcr(line.text) === '分享(1)'
    && inside(line, footerFrame)), 'LINE_FORWARD_UI_UNVERIFIED', 'Exact Qt Share(1) label');
  return { edit, selected: selectedRows[0], finalPixel: center(finalLabel),
    fingerprint: hash({ query: edit.value, selected: 1,
      recipient: recipient.chatName, kind: selectedRows[0].chatType ?? recipient.chatType,
      chip: qtFrame(chip[0], state, window, dimensions), final: compactOcr(finalLabel.text) }) };
}

async function listDialog(api, sourceTarget, recipient, priorWindows, ui) {
  const result = await api.call('list_windows', {});
  if (!Array.isArray(result?.windows)) fail('LINE_FORWARD_UI_UNVERIFIED', 'LINE windows could not be enumerated.');
  const known = new Set((priorWindows ?? []).filter(window => window?.is_on_screen === true)
    .map(window => `${window.pid}:${window.window_id}`));
  const candidates = [];
  for (const window of result.windows) {
    if (window?.app_name !== 'LINE.exe' || window.pid !== sourceTarget.pid
      || window.is_on_screen !== true || window.minimized === true
      || !Number.isInteger(window.window_id)) continue;
    const target = { pid: window.pid, window_id: window.window_id };
    if (!sameTarget(target, sourceTarget) && known.has(`${target.pid}:${target.window_id}`)) continue;
    let state;
    try { state = await snapshot(api, target, { screenshot: true }); } catch { continue; }
    let mode;
    try { dialogProof(state, recipient, { windowTitle: window.title }); mode = 'semantic'; }
    catch {
      try { await qtDialogProof(state, window, recipient, ui); mode = 'qt'; }
      catch { continue; }
    }
    candidates.push({ target, state, window, mode });
  }
  return { ...one(candidates, 'LINE_FORWARD_UI_UNVERIFIED', 'Share dialog window'),
    listedWindows: result.windows };
}

function sessionCopy(session) { return JSON.parse(JSON.stringify(session)); }
function notReady(error, currentStage, state) {
  return { stage: 'PREPARE_NOT_READY', reason: error?.code ?? 'LINE_FORWARD_UI_UNVERIFIED',
    currentStage, images: images(state), evidence: { sendDispatched: false,
      detail: error?.message ?? null,
      operationMayHaveCompleted: error?.details?.operationMayHaveCompleted === true } };
}

/** Original-message forwarding, gated by fresh UI evidence and a process-local
 * prepared record. It never reuploads a file or reconstructs message text. */
export function createForwardUi(ui, { now = Date.now, randomToken = () => randomBytes(24).toString('base64url') } = {}) {
  if (typeof ui?.withForwardSourceChat !== 'function' || typeof ui?.withClient !== 'function')
    throw new TypeError('ui must provide the guarded LINE source-chat and CUA clients.');
  const prepared = new Map();

  function checked(binding, session) {
    const digest = bindingDigest(binding);
    const stored = prepared.get(session?.id);
    if (!stored || hash(session) !== hash(stored) || digest !== stored.bindingDigest
      || stored.phase !== 'PREPARED' || now() > stored.expiresAt) {
      fail('LINE_FORWARD_PREPARED_STALE', 'The prepared share state expired, changed, or belonged to another process.');
    }
    return stored;
  }

  async function inspectReady(api, binding, session) {
    await assertRecipientIdentity(ui, binding.recipient);
    const listed = await api.call('list_windows', {});
    const exact = listed?.windows?.filter(window => window?.app_name === 'LINE.exe'
      && window.is_on_screen === true && window.minimized !== true
      && sameTarget(window, session.dialogTarget));
    if (!Array.isArray(exact) || exact.length !== 1 || exact[0].title !== session.dialogTitle) {
      fail('LINE_FORWARD_PREPARED_STALE', 'The prepared LINE share window changed.');
    }
    const state = await snapshot(api, session.dialogTarget, { screenshot: true });
    const proof = session.dialogMode === 'qt'
      ? await qtDialogProof(state, exact[0], binding.recipient, ui, 'selected')
      : (dialogProof(state, binding.recipient, { selected: true, windowTitle: session.dialogTitle }),
        { fingerprint: reviewFingerprint(state, binding.recipient) });
    if (proof.fingerprint !== session.reviewFingerprint) {
      fail('LINE_FORWARD_PREPARED_STALE', 'The selected-recipient review changed after preparation.');
    }
    return { state, proof };
  }

  async function inspectCancelTarget(api, binding, session) {
    const listed = await api.call('list_windows', {});
    const exact = listed?.windows?.filter(window => window?.app_name === 'LINE.exe'
      && window.is_on_screen === true && window.minimized !== true
      && sameTarget(window, session.dialogTarget));
    if (!Array.isArray(exact) || exact.length !== 1 || exact[0].title !== session.dialogTitle) {
      fail('LINE_FORWARD_CANCEL_STALE', 'The prepared Share dialog window changed before cancellation.');
    }
    const state = await snapshot(api, session.dialogTarget, { screenshot: true });
    if (session.dialogMode === 'qt') await qtDialogProof(state, exact[0], binding.recipient, ui);
    else dialogProof(state, binding.recipient, { windowTitle: session.dialogTitle });
    return state;
  }

  async function dialogIsClosed(api, session) {
    const listed = await api.call('list_windows', {});
    const exact = listed?.windows?.filter(window => window?.app_name === 'LINE.exe'
      && window.is_on_screen === true && window.minimized !== true
      && sameTarget(window, session.dialogTarget));
    if (!Array.isArray(exact)) return false;
    if (exact.length === 0) return true;
    if (exact.length !== 1 || exact[0].title !== session.dialogTitle) return false;
    let state;
    try { state = await snapshot(api, session.dialogTarget, { screenshot: false }); }
    catch { return false; }
    if (session.dialogMode === 'qt') return false;
    try { dialogProof(state, {}, { windowTitle: session.dialogTitle }); return false; }
    catch { return !SHARE_LABELS.has(session.dialogTitle); }
  }

  return {
    async prepare(binding, session = null) {
      bindingDigest(binding);
      if (session !== null) {
        const ready = await this.assertReady(binding, session);
        return { stage: 'PREPARED', session: sessionCopy(session),
          challenge: { recipient: binding.recipient, source: binding.source },
          images: ready.images, evidence: ready.evidence };
      }
      let stage = 'SOURCE_SELECTION_REQUIRED';
      let latest;
      try {
        await assertRecipientIdentity(ui, binding.recipient);
        return await ui.withForwardSourceChat(binding, async (api, inspected) => {
          latest = inspected.state;
          const firstSource = await sourceTarget(inspected.state, binding, inspected, ui);
          const freshSource = async state => {
            const fresh = await sourceTarget(state, binding, inspected, ui);
            if (!!fresh.pixel !== !!firstSource.pixel
              || JSON.stringify(fresh.inlineSharePixel ?? null)
                !== JSON.stringify(firstSource.inlineSharePixel ?? null)
              || (fresh.pixel && (JSON.stringify(fresh.pixel) !== JSON.stringify(firstSource.pixel)
                || fresh.fingerprint.sha256 !== firstSource.fingerprint.sha256
                || JSON.stringify(fresh.fingerprint.region)
                  !== JSON.stringify(firstSource.fingerprint.region)))) {
              fail('LINE_FORWARD_SOURCE_UI_STALE', 'The source card moved or changed before Share.');
            }
            return fresh;
          };
          let dialog;
          if (firstSource.inlineSharePixel) {
            // The verified file row itself exposes an exact inline Share link.
            // Reprove both card pixels and link words before one foreground click.
            try {
              const clicked = await runUiInput(api, inspected.target,
                async state => ({ pixel: (await freshSource(state)).inlineSharePixel }),
                'click', {}, { screenshot: true, guard: inspected.guard,
                  deliveryMode: 'foreground' });
              latest = clicked.after;
            } catch (error) {
              if (error?.code !== 'LINE_UI_ACTION_REFUSED'
                || error.details?.operationMayHaveCompleted !== true
                || !(error.details?.backendCode === 'foreground_unavailable'
                  || (error.details?.backendCode == null
                    && /^CUA click: foreground_unavailable:/u.test(error.message ?? '')))) throw error;
              // The source HWND stays open; prove the newly opened selector
              // instead of replaying an uncertain inline Share click.
              dialog = await listDialog(api, inspected.target, binding.recipient,
                inspected.lineWindows, ui);
            }
            stage = 'RECIPIENT_SELECTION_REQUIRED';
          } else {
            const opened = await runUiInput(api, inspected.target, freshSource,
              'right_click', {}, { screenshot: true, guard: inspected.guard,
                deliveryMode: firstSource.pixel ? 'foreground' : 'background' });
            latest = opened.after;
            stage = 'SHARE_MENU_SELECTION_REQUIRED';
            const menu = await resolveOpenedMenu(api, inspected.lineWindows, opened,
              [...SHARE_LABELS], 'original-message Share action', 'LINE_FORWARD_UI_UNVERIFIED', ui.ocr);
            let chosen;
            try {
              chosen = await runUiInput(api, menu.target,
                state => menuItemTarget(state, [...SHARE_LABELS], 'Share menu item',
                  'LINE_FORWARD_UI_UNVERIFIED', menu.proof, ui.ocr, menu.window),
                'click', {}, { screenshot: true,
                  deliveryMode: menu.proof ? 'foreground' : 'background',
                  allowTargetClosedAfter: !sameTarget(menu.target, inspected.target) });
            } catch (error) {
              const foregroundRefusal = error?.details?.backendCode === 'foreground_unavailable'
                || (error?.details?.backendCode == null
                  && /^CUA click: foreground_unavailable:/u.test(error?.message ?? ''));
              if (error?.code !== 'LINE_UI_ACTION_REFUSED'
                || error.details?.operationMayHaveCompleted !== true
                || !foregroundRefusal
                || sameTarget(menu.target, inspected.target)) throw error;
              dialog = await listDialog(api, inspected.target, binding.recipient,
                inspected.lineWindows, ui);
              if (dialog.listedWindows.some(window => window.is_on_screen === true
                && window.minimized !== true && sameTarget(window, menu.target))) throw error;
            }
            latest = chosen?.after ?? latest;
            stage = 'RECIPIENT_SELECTION_REQUIRED';
          }
          dialog ??= await listDialog(api, inspected.target, binding.recipient,
            inspected.lineWindows, ui);
          latest = dialog.state;
          await runUiInput(api, dialog.target,
            async state => dialog.mode === 'qt'
              ? { element: (await qtDialogProof(state, dialog.window, binding.recipient, ui)).edit }
              : (dialogProof(state, binding.recipient, { windowTitle: dialog.window.title }),
                { element: searchControl(state) }),
            'set_value', { value: binding.recipient.chatName }, { screenshot: true });
          const found = await runUiInput(api, dialog.target,
            async state => dialog.mode === 'qt'
              ? { element: (await qtDialogProof(state, dialog.window, binding.recipient, ui, 'results')).result }
              : (dialogProof(state, binding.recipient, { windowTitle: dialog.window.title }),
                { element: recipientResult(state, binding.recipient) }),
            'click', {}, { screenshot: true, deliveryMode: dialog.mode === 'qt' ? 'foreground' : 'background' });
          latest = found.after;
          stage = 'RECIPIENT_REVIEW_REQUIRED';
          const reviewed = await snapshot(api, dialog.target, { screenshot: true });
          const review = dialog.mode === 'qt'
            ? await qtDialogProof(reviewed, dialog.window, binding.recipient, ui, 'selected')
            : (dialogProof(reviewed, binding.recipient, { selected: true, windowTitle: dialog.window.title }),
              { fingerprint: reviewFingerprint(reviewed, binding.recipient) });
          latest = reviewed;
          const issuedAt = now();
          const entry = { id: randomToken(), phase: 'PREPARED', bindingDigest: bindingDigest(binding),
            sourceTarget: inspected.target, dialogTarget: dialog.target, dialogTitle: dialog.window.title,
            dialogMode: dialog.mode, reviewFingerprint: review.fingerprint,
            issuedAt, expiresAt: issuedAt + SESSION_MS };
          prepared.set(entry.id, sessionCopy(entry));
          return { stage: 'PREPARED', session: sessionCopy(entry),
            challenge: { source: binding.source, recipient: binding.recipient,
              selectedCount: 1, expiresAt: new Date(entry.expiresAt).toISOString() },
            images: images(reviewed), evidence: { sourceUiProof: firstSource.proofKind,
              shareMenuProof: firstSource.inlineSharePixel ? 'exact-source-row-inline-share'
                : 'unique-exact-menu-item', recipientUiProof: dialog.mode === 'qt'
                ? 'fresh-qt-search-selected-row-one-chip-share-count' : 'unique-exact-name-kind-selected',
              finalShareVisible: true, sendDispatched: false } };
        });
      } catch (error) {
        if (error instanceof LineToolError) return notReady(error, stage, latest);
        throw error;
      }
    },

    async assertReady(binding, session) {
      checked(binding, session);
      return ui.withClient(async api => {
        const { state } = await inspectReady(api, binding, session);
        return { stage: 'PREPARED', images: images(state),
          evidence: { selectedCount: 1, recipient: binding.recipient, finalShareVisible: true,
            sendDispatched: false } };
      });
    },

    async dispatch(binding, session, beforeDispatch) {
      if (typeof beforeDispatch !== 'function') throw new TypeError('beforeDispatch must journal the commit before the final Share click.');
      const entry = checked(binding, session);
      // Consume the process-local permission before entering the uncertain
      // CUA action. A timeout/refusal can never replay the final Share click.
      prepared.delete(entry.id);
      return ui.withClient(async api => {
        const { state, proof } = await inspectReady(api, binding, entry);
        const button = entry.dialogMode === 'qt' ? null : finalShare(state);
        await beforeDispatch();
        let result;
        try {
          result = await api.call('click', entry.dialogMode === 'qt'
            ? { ...entry.dialogTarget, ...proof.finalPixel, delivery_mode: 'foreground' }
            : { ...elementTarget(entry.dialogTarget, state, button), delivery_mode: 'background' });
        } catch (error) {
          if (error instanceof LineToolError) {
            error.details = { ...error.details, operationMayHaveCompleted: true, sendDispatched: 'uncertain' };
          }
          throw error;
        }
        return { dispatched: true, sendDispatched: true, result,
          evidence: { recipient: binding.recipient, finalShareClickedOnce: true } };
      });
    },

    async cancel(binding, session) {
      const entry = checked(binding, session);
      return ui.withClient(async api => {
        await inspectCancelTarget(api, binding, entry);
        if (!(api.tools instanceof Set) || !api.tools.has('press_key')) {
          return { closed: false, reason: 'LINE_FORWARD_CANCEL_CAPABILITY_UNAVAILABLE' };
        }
        // Consume before the one Escape. A lost acknowledgment must never
        // trigger another keypress, and the key is targeted to the proven
        // Share dialog HWND rather than the source chat by name.
        prepared.delete(entry.id);
        try {
          await api.call('press_key', { ...entry.dialogTarget, key: 'escape',
            delivery_mode: entry.dialogMode === 'qt' ? 'foreground' : 'background' });
        } catch (error) {
          if (error instanceof LineToolError) {
            error.details = { ...error.details, operationMayHaveCompleted: true };
          }
          throw error;
        }
        const closed = await dialogIsClosed(api, entry);
        return { closed, ...(closed ? {} : { reason: 'LINE_FORWARD_CANCEL_UNVERIFIED' }) };
      });
    },
  };
}
