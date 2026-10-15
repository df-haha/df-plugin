import { createHash } from 'node:crypto';
import { LineToolError, requireChat } from './line-runtime.mjs';
import { readLocalLineBoundDirectMessages, runReaderProcess } from './line-local-reader.mjs';
import { snapshot } from './cua-line-client.mjs';
import { inspectDetachedChat, createChatGuard, composerOptions, findComposer, replySourceVisualView } from './line-ui.mjs';
import { canonicalOcrText } from './line-ocr.mjs';

const day = time => new Date(time + 28800000).toISOString().slice(0, 10);
const hash = value => createHash('sha256').update(JSON.stringify(value)).digest('hex');
const fail = (code = 'LINE_BOUND_CONTEXT_UNVERIFIED') => {
  throw new LineToolError(code, 'The selected direct chat could not be independently verified.', { sendDispatched: false });
};
const inside = (a, b) => a && b && [a.x,a.y,a.width,a.height].every(Number.isFinite)
  && a.width > 0 && a.height > 0 && a.x >= b.x && a.y >= b.y
  && a.x + a.width <= b.x + b.width + 1 && a.y + a.height <= b.y + b.height + 1;
const compact = value => canonicalOcrText(value).replace(/\s/gu, '');
const text = value => canonicalOcrText(value.replace(/\r\n/gu, '\n'));

export const hasBoundDirectRefs = args => args.chatType === 'direct'
  && args.expectedChatRef !== undefined && args.expectedOwnSenderRef !== undefined;

export function boundDirectScope(args, now = Date.now()) {
  requireChat(args.chatName);
  if (!hasBoundDirectRefs(args) || !/^chat:[0-9a-f]{24}$/u.test(args.expectedChatRef)
    || !/^sender:[0-9a-f]{24}$/u.test(args.expectedOwnSenderRef)) fail('LINE_INVALID_ARGUMENT');
  return { chatName: args.chatName, chatType: 'direct', expectedChatRef: args.expectedChatRef,
    expectedOwnSenderRef: args.expectedOwnSenderRef, dateFrom: day(now - 2 * 86400000),
    dateTo: day(now), messageLimit: 30, mediaMode: 'metadata' };
}

export function boundDirectTail(result, scope, now = Date.now()) {
  if (result?.chatRef !== scope.expectedChatRef) fail('CHAT_IDENTITY_CHANGED');
  if (result.ownSenderRef !== scope.expectedOwnSenderRef) fail('CHAT_ACCOUNT_CHANGED');
  const captured = Date.parse(result.freshness?.snapshotCapturedAt);
  if (result.ok !== true || result.chatName !== scope.chatName || result.chatIdentity?.kind !== 'direct'
    || result.chatIdentity.knownNameUnique !== true || result.chatIdentity.guiDisplayNameUnique !== false
    || result.freshness?.clockOrderValid !== true || !Number.isFinite(captured)
    || captured > now + 5000 || now - captured > 30000 || result.scope?.kind !== 'local_database'
    || result.pagination?.limitedBy === 'response_bytes' || !Array.isArray(result.messages)
    || result.messages.length < 2 || result.messages.length > 30) fail();
  const peer = `sender:${scope.expectedChatRef.slice(5)}`;
  const tail = result.messages.slice(-2).map(message => {
    if (message.contentType !== 0 || typeof message.text !== 'string' || !message.text.trim()
      || !/^message:[0-9a-f]{24}$/u.test(message.sourceRef ?? '')
      || !Number.isSafeInteger(message.sourceTimestamp) || day(message.sourceTimestamp) !== message.date
      || message.date < scope.dateFrom || message.date > scope.dateTo
      || !/^\d{2}:\d{2}:\d{2}$/u.test(message.time ?? '')
      || new Date(message.sourceTimestamp + 28800000).toISOString().slice(11,19) !== message.time
      || ![peer, scope.expectedOwnSenderRef].includes(message.senderRef)) fail();
    return { sourceRef:message.sourceRef, text:text(message.text), date:message.date,
      minute:message.time.slice(0,5), direction:message.senderRef === scope.expectedOwnSenderRef ? 'outgoing' : 'incoming',
      timestamp:message.sourceTimestamp };
  });
  if (tail[0].timestamp > tail[1].timestamp || tail[0].date !== tail[1].date
    || tail[0].text === tail[1].text) fail();
  // Short messages are admissible only as distinct full text/time/direction
  // tuples, unique in this newest local page, never as one generic "OK".
  for (const anchor of tail) {
    const matches = result.messages.filter(message => message.contentType === 0
      && typeof message.text === 'string' && text(message.text) === anchor.text
      && message.date === anchor.date && message.time?.slice(0,5) === anchor.minute
      && (message.senderRef === scope.expectedOwnSenderRef ? 'outgoing' : 'incoming') === anchor.direction);
    if (matches.length !== 1) fail();
  }
  return tail;
}

function minute(value) {
  const match = /^(上午|下午)?(\d{1,2}):(\d{2})$/u.exec(compact(value));
  if (!match) return null;
  let hour = Number(match[2]);
  if (Number(match[3]) > 59 || hour > (match[1] ? 12 : 23) || (match[1] && hour < 1)) return null;
  if (match[1]) hour = hour % 12 + (match[1] === '下午' ? 12 : 0);
  return `${String(hour).padStart(2,'0')}:${match[3]}`;
}

export function visibleDate(value, expected, now = Date.now()) {
  const label = compact(value);
  if (label === '今天') return day(now);
  if (label === '昨天') return day(now - 86400000);
  // OCR may omit the opening weekday parenthesis; every date digit and the
  // weekday itself must still be independently present and agree.
  const match = /^(?:(\d{4})[年\/-])?(\d{1,2})[月\/-](\d{1,2})(?:日)?(?:[（(]?(?:週|星期)?([一二三四五六日天])[）)])?$/u.exec(label);
  if (!match) return null;
  const date = `${match[1] ?? expected.slice(0,4)}-${match[2].padStart(2,'0')}-${match[3].padStart(2,'0')}`;
  const parsed = Date.parse(`${date}T00:00:00Z`);
  if (!Number.isFinite(parsed) || new Date(parsed).toISOString().slice(0,10) !== date) return null;
  if (match[4] && '日一二三四五六'[new Date(parsed).getUTCDay()] !== match[4].replace('天','日')) return null;
  return date;
}

function rowFrame(item, view) {
  const f = item.frame, root = view.rootFrame;
  if (!f || ![f.x,f.y,f.w,f.h].every(Number.isFinite)) return null;
  const sx = view.dimensions.width / root.width, sy = view.dimensions.height / root.height;
  return {x:Math.floor((f.x-root.x)*sx), y:Math.floor((f.y-root.y)*sy),
    width:Math.floor(f.w*sx), height:Math.floor(f.h*sy)};
}

export function matchBoundDirectRows(rows, tail, date, bounds) {
  if (date !== tail[0]?.date || rows.length < 2) fail();
  const selected = rows.slice(-2);
  if (rows.some((row,i) => !inside(row.frame,bounds)
    || (i > 0 && row.frame.y < rows[i-1].frame.y + rows[i-1].frame.height - 2))) fail();
  const proof = selected.map((row,index) => {
    const content = row.lines.filter(line => text(line.text ?? '') === tail[index].text);
    if (content.length !== 1 || !inside(content[0],row.frame)
      || content[0].x < row.frame.x + 8 || content[0].x + content[0].width > row.frame.x + row.frame.width - 8) fail();
    const clocks = row.lines.filter(line => minute(line.text ?? '') !== null);
    if (clocks.length > 1) fail();
    if (row.lines.some(line => line !== content[0] && !clocks.includes(line)
      && !/^(?:已讀\d*|Read\d*)$/u.test(compact(line.text ?? '')))) fail();
    const clock = clocks[0];
    const center = bounds.x + bounds.width/2, label = content[0];
    const side = label.x > center ? 'outgoing' : label.x + label.width < center ? 'incoming' : null;
    if (!side || side !== tail[index].direction) fail();
    if (clock && (minute(clock.text) !== tail[index].minute
      || (side === 'outgoing' ? clock.x + clock.width > label.x : clock.x < label.x + label.width))) fail();
    return {minute:clock ? minute(clock.text) : null, source:clock ? 'observed' : null};
  });
  if (!proof[0].minute && proof[1].minute && tail[0].minute === tail[1].minute
    && tail[0].direction === tail[1].direction
    && Math.abs(selected[1].frame.y-selected[0].frame.y-selected[0].frame.height) <= 4) {
    proof[0] = {minute:proof[1].minute,source:'adjacent-shared'};
  }
  if (proof.some(item => !item.minute)) fail();
  return { rows:selected, minuteSources:proof.map(item=>item.source) };
}

/** Inspect an already-open exact titled chat. This function has no input calls. */
export async function inspectBoundDirect(ui, api, scope, before, readCurrent, check = () => {}) {
  const tail = boundDirectTail(before,scope);
  const inspected = await inspectDetachedChat(api,scope.chatName,{screenshot:true});
  if (!inspected) fail('LINE_BOUND_WINDOW_REQUIRED');
  const titleGuard = createChatGuard(scope.chatName,inspected,ui.ocr,ui.visual);
  const view = replySourceVisualView(inspected.state,inspected.window,titleGuard,ui.visual);
  if (!view) fail();
  const ocr = await ui.ocr.recognizeImage(view.image);
  if (ocr.coordinateSpace !== 'input-png-pixels' || ocr.scaleFactor !== 1
    || ocr.width !== view.dimensions.width || ocr.height !== view.dimensions.height) fail();
  const rows = inspected.state.elements.filter(item=>item.role==='ListItem')
    .map(item=>({item,frame:rowFrame(item,view)})).filter(row=>inside(row.frame,view.messageBounds))
    .sort((a,b)=>a.frame.y-b.frame.y).map(row=>({...row,lines:ocr.lines.filter(line=>inside(line,row.frame))}));
  const selected = rows.slice(-2);
  if (selected.length !== 2) fail();
  const dateRows = rows.filter(row=>row.frame.y < selected[0].frame.y && row.frame.height <= 50
    && row.lines.some(line=>line.width < 200 && Math.abs(line.x+line.width/2-view.dimensions.width/2)<100
      && /\d+\s*月|今天|昨天|\d{4}[\/-]/u.test(line.text)));
  const dateRow = dateRows.at(-1);
  if (!dateRow) fail();
  let dates = dateRow.lines.map(line=>visibleDate(line.text,tail[0].date)).filter(Boolean);
  if (dates.length !== 1) {
    // Small grey date glyphs need a bounded high-resolution OCR pass, never
    // a guessed substitution for a missing/misread date.
    const region={x:Math.max(dateRow.frame.x,Math.floor(view.dimensions.width/2)-125),y:dateRow.frame.y,
      width:250,height:dateRow.frame.height};
    const crop=await ui.visual.fingerprintRegion(view.image,region,{includeImage:true});
    const dateOcr=await ui.ocr.recognizeImage(crop.image,{upscaleFactor:3});
    dates=dateOcr.lines.map(line=>visibleDate(line.text,tail[0].date)).filter(Boolean);
  }
  if(dates.length!==1)fail();
  const matched=matchBoundDirectRows(rows,tail,dates[0],view.messageBounds);
  const regions=[dateRow.frame,...matched.rows.map(row=>row.frame)];
  const fingerprints=await Promise.all(regions.map(region=>ui.visual.fingerprintRegion(view.image,region)));
  const tailDigest=hash(tail), initialWindow=inspected.window;
  const guard={kind:'bound-direct-context',chatName:scope.chatName,target:inspected.target,
    async verify(state) {
      check();
      const windows=await api.call('list_windows',{pid:inspected.target.pid});
      const exact=windows.windows.filter(window=>window.app_name==='LINE.exe' && window.title===scope.chatName
        && window.is_on_screen && !window.minimized);
      if(exact.length!==1 || exact[0].window_id!==inspected.target.window_id
        || ['x','y','width','height'].some(key=>exact[0].bounds?.[key]!==initialWindow.bounds?.[key])) fail('LINE_CHAT_STALE');
      const current=await readCurrent();
      if(hash(boundDirectTail(current,scope))!==tailDigest)fail('LINE_BOUND_CONTEXT_CHANGED');
      // A local read can take seconds; bind pixels to a new observation after it.
      state=await snapshot(api,inspected.target,{screenshot:true});
      const fresh=replySourceVisualView(state,initialWindow,titleGuard,ui.visual);
      if(!fresh || fresh.dimensions.width!==view.dimensions.width || fresh.dimensions.height!==view.dimensions.height)fail();
      const next=await Promise.all(regions.map(region=>ui.visual.fingerprintRegion(fresh.image,region)));
      if(next.some((item,index)=>item.sha256!==fingerprints[index].sha256))fail('LINE_BOUND_CONTEXT_CHANGED');
      check();
      return state;
    }};
  return {inspected,guard,titleGuard,options:composerOptions(inspected,guard),
    evidence:{proofKind:'bound-direct-recent-context',matchedMessageCount:tail.length,dateVerified:true,minuteSources:matched.minuteSources}};
}

export async function prepareBoundDirect(ui,args,{readMessages=readLocalLineBoundDirectMessages,now=Date.now}={}) {
  const scope=boundDirectScope(args,now());
  const deadline=Date.now()+30000;
  const check=()=>{if(Date.now()>=deadline)fail('LINE_PREPARE_TIMEOUT');};
  const read=()=>{check();return readMessages(scope,{runProcess:request=>runReaderProcess(request,
    {timeoutMs:Math.max(1,Math.min(5000,deadline-Date.now()))})});};
  const before=await read();
  // Check the opaque refs before any GUI access, even for injected readers.
  boundDirectTail(before,scope,now());
  return ui.withClient(async api=>{
    const proof=await inspectBoundDirect(ui,api,scope,before,read,check);
    const fresh=await proof.guard.verify(proof.inspected.state);
    if(findComposer(fresh,proof.options).value!=='')fail('LINE_DRAFT_CONFLICT');
    return {success:true,status:'READY',chatName:args.chatName,chatType:'direct',chatRef:args.expectedChatRef,
      ownSenderRef:args.expectedOwnSenderRef,checkedAt:new Date(now()).toISOString(),guiVerified:true,
      contextVerified:true,draftEmpty:true,staged:false,sendDispatched:false,evidence:proof.evidence};
  },{deadline});
}
