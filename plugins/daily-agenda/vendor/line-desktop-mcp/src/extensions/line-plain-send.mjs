import { createHash, randomUUID } from 'node:crypto';
import fs from 'node:fs/promises';
import path from 'node:path';
import { homedir } from 'node:os';
import { LineToolError } from './line-runtime.mjs';
import { readLocalLineMessages, readLocalLineChatIdentity, readLocalLineBoundDirectMessages, runReaderProcess } from './line-local-reader.mjs';
import { hasBoundDirectRefs, boundDirectScope, inspectBoundDirect } from './line-bound-direct.mjs';
import { mainLineWindow, snapshot, elementTarget } from './cua-line-client.mjs';
import { purepngDimensions } from './line-ocr.mjs';
import { navigationOcrRegion, searchResultsOcrRegion, singleDirectSearchOcrRegion, mixedDirectSearchOcrRegions,
  recognizeScopedLineImage,
  combineNavigationAndResults } from './line-scoped-ocr.mjs';
import { exactCategoryTab } from './line-category-tab.mjs';
import { exactSearchResult, singleGroupSearchCandidate, singleDirectSearchCandidate,
  mixedDirectSearchCandidate } from './line-exact-search.mjs';
import { directSearchLayout } from './line-group-navigation.mjs';
import { inspectDetachedChat, createChatGuard, composerOptions,
  writeDraft, runUiInput, findComposer, findMainChatBands, findContentHeaderContainer } from './line-ui.mjs';

const digest = value => createHash('sha256').update(value).digest('hex');
const day = time => new Date(time + 8 * 3600000).toISOString().slice(0, 10);
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
const frame = e => e?.frame && { x:e.frame.x, y:e.frame.y, width:e.frame.w, height:e.frame.h };
const fail = (code, message) => { throw new LineToolError(code, message, {sendDispatched:false}); };

// The existing cross-process operation lock also serializes these journal writes.
const journalRoot = () => path.join(process.env.LOCALAPPDATA || homedir(), 'line-desktop-mcp', 'sends');
async function loadRecord(file) {
  try { return JSON.parse(await fs.readFile(file, 'utf8')); }
  catch (e) { if(e.code === 'ENOENT') return null; throw e; }
}
async function saveRecord(file, record) {
  await fs.mkdir(path.dirname(file), {recursive:true});
  const temporary = `${file}.${randomUUID()}.tmp`;
  const handle = await fs.open(temporary, 'wx');
  try { await handle.writeFile(JSON.stringify(record)); await handle.sync(); }
  finally { await handle.close(); }
  await fs.rename(temporary, file);
}

export function matchReceipt(before, after, message, startedAt) {
  const capturedAt = Date.parse(after.freshness?.snapshotCapturedAt);
  if(after.chatRef !== before.chatRef || after.ownSenderRef !== before.ownSenderRef
    || !after.freshness?.clockOrderValid
    || !Number.isFinite(capturedAt) || capturedAt < startedAt) return null;
  const old = new Set(before.messages.map(m=>m.sourceRef));
  const latest = before.messages.at(-1);
  if(latest && (after.messages.length >= 50 || after.pagination?.hasMore === true)
    && !after.messages.some(m=>m.sourceRef===latest.sourceRef)) return null;
  const matches = after.messages.filter(m=>m.senderRef===before.ownSenderRef && m.text===message
    && m.contentType===0 && !old.has(m.sourceRef)
    && m.sourceTimestamp >= Math.max(startedAt-5000, latest?.sourceTimestamp ?? 0));
  return matches.length === 1 ? matches[0] : null;
}

export async function readPlainIdentity({chatName}, {readIdentity=readLocalLineChatIdentity}={}) {
  const date = day(Date.now());
  const result = await readIdentity({chatName, chatType:'auto', dateFrom:date, dateTo:date,
    requireUniqueName:true},
    {runProcess:scope=>runReaderProcess(scope,{timeoutMs:5000})});
  if(!['direct','group'].includes(result.chatIdentity?.kind)
    || !/^chat:[0-9a-f]{24}$/u.test(result.chatRef ?? ''))
    fail('LINE_CHAT_IDENTITY_UNVERIFIED','No unique local chat identity was resolved.');
  return Object.freeze({chatRef:result.chatRef,kind:result.chatIdentity.kind});
}

export async function openExactChat(ui, api, chatName, chatType, check) {
  let inspected = await inspectDetachedChat(api, chatName);
  if(inspected) return inspected;
  let window = await mainLineWindow(api);
  const target = {pid:window.pid, window_id:window.window_id};
  if((await ui.automation.activateLine({ ...target, title:window.title }))?.success!==true) fail('LINE_FOCUS_UNAVAILABLE','LINE could not be activated.');
  const activated = await mainLineWindow(api);
  if(activated.pid!==target.pid || activated.window_id!==target.window_id
    || activated.minimized || !activated.is_on_screen
    || (!window.minimized && ['x','y','width','height'].some(key=>activated.bounds?.[key]!==window.bounds?.[key])))
    fail('LINE_TARGET_CHANGED','The verified LINE main window changed after activation.');
  window = activated;
  check();
  let state = await snapshot(api,target,{screenshot:true});
  if(!state.images?.[0]) fail('LINE_CHAT_UNVERIFIED','LINE did not provide the screenshot needed to verify the requested chat.');
  const bounds = window.bounds;
  const size=purepngDimensions(state.images[0]);
  const recognized=await recognizeScopedLineImage(state.images[0],
    navigationOcrRegion(state,window,size),size);
  const tab=exactCategoryTab(recognized,state.elements,bounds,size,chatType);
  if(!tab) fail('LINE_CATEGORY_UNAVAILABLE','The requested LINE category has no unique visible UI tab.');
  await api.call('click',{...elementTarget(target,state,tab),delivery_mode:'foreground'});
  state=await snapshot(api,target,{screenshot:true});
  const search = state.elements.filter(e=>e.role==='Edit' && frame(e)?.x < bounds.x+bounds.width/2
    && frame(e)?.y < bounds.y+160 && frame(e)?.height<70 && frame(e)?.width>100);
  if(search.length!==1) fail('LINE_SEARCH_UNAVAILABLE','No unique LINE search box.');
  await api.call('set_value',{...elementTarget(target,state,search[0]),value:chatName});
  state = await snapshot(api,target,{screenshot:true});
  let result;
  for(let attempt=0;attempt<4;attempt++) {
    check();
    try {
      const image=state.images?.[0];
      const dimensions=purepngDimensions(image);
      if(chatType==='group') {
        try { result=singleGroupSearchCandidate(state,window,dimensions,chatName); }
        catch { result=null; }
      }
      if(!result) {
        const nav=await recognizeScopedLineImage(image,navigationOcrRegion(state,window,dimensions),dimensions);
        const layout=directSearchLayout(state,window,dimensions);
        const list=state.elements.find(e=>e.role==='List' && frame(e)?.x===layout.geometry.list.x
          && frame(e)?.y===layout.geometry.list.y && frame(e)?.height===layout.geometry.list.height);
        const rows=state.elements.filter(e=>e.role==='ListItem'
          && e.parent_index===list?.element_index).sort((a,b)=>frame(a).y-frame(b).y);
        if(chatType==='direct' && rows.length===2) {
          try {
            const category=await recognizeScopedLineImage(image,
              singleDirectSearchOcrRegion(state,window,dimensions,chatName),dimensions);
            result=singleDirectSearchCandidate(state,window,dimensions,
              combineNavigationAndResults(nav,category,dimensions),chatName);
          } catch { result=null; }
          if(result) break;
        }
        const mixed=chatType==='direct' && rows.slice(2).some(e=>frame(e).height<45);
        if(mixed) {
          const regions=mixedDirectSearchOcrRegions(state,window,dimensions,chatName);
          const category=await recognizeScopedLineImage(image,regions.chatHeader,dimensions,{paddingPixels:16});
          const messages=await recognizeScopedLineImage(image,regions.messageHeader,dimensions,{paddingPixels:16});
          const title=await recognizeScopedLineImage(image,regions.title,dimensions,{paddingPixels:16});
          const labels={...category,lines:[...category.lines,...messages.lines]};
          const ocr=combineNavigationAndResults(nav,labels,dimensions);
          ocr.lines.push(...title.lines);
          result=mixedDirectSearchCandidate(state,window,dimensions,ocr,chatName);
        } else {
          const results=await recognizeScopedLineImage(image,
            searchResultsOcrRegion(state,window,dimensions),dimensions);
          const ocr=combineNavigationAndResults(nav,results,dimensions);
          if(chatType==='direct') {
            try { result=exactSearchResult(state,window,dimensions,ocr,chatName,chatType); }
            catch { result=singleDirectSearchCandidate(state,window,dimensions,ocr,chatName); }
          } else result=exactSearchResult(state,window,dimensions,ocr,chatName,chatType);
        }
      }
      break;
    }catch{result=null;}
    if(attempt<3){await pause(200);state=await snapshot(api,target,{screenshot:true});}
  }
  if(!result) fail('LINE_SEARCH_NOT_UNIQUE','Search did not prove one safe result candidate in the current LINE category.');
  check();
  await api.call('click',{...elementTarget(target,state,result),delivery_mode:'foreground'});
  state=await snapshot(api,target,{screenshot:true});
  const bands=findMainChatBands(state);
  const header=bands.length===1 && findContentHeaderContainer(state,bands[0]);
  if(!header) fail('LINE_HEADER_UNAVAILABLE','The selected chat header is unavailable.');
  // Current verified LINE Qt layout: title, mute, open-individual-window.
  // The latter two are adjacent 20 x 24 controls; no whole-body image matching.
  const controls=state.elements.filter(e=>e.parent_index===header.element_index && e.role==='Group')
    .sort((a,b)=>frame(a).x-frame(b).x);
  const title=controls[0], count=chatType==='group'?controls[1]:null;
  const mute=controls[chatType==='group'?2:1], detach=controls[chatType==='group'?3:2];
  if(!title || !mute || !detach || frame(mute).width!==20 || frame(detach).width!==20
    || frame(mute).height!==24 || frame(detach).height!==24
    || frame(detach).x!==frame(mute).x+20
    || (count ? frame(count).height!==frame(title).height
      || Math.abs(frame(count).x-frame(title).x-frame(title).width)>1
      || frame(mute).x!==frame(count).x+frame(count).width
      : frame(mute).x!==frame(title).x+frame(title).width))
    fail('LINE_DETACH_UNAVAILABLE','The individual-window control does not match the supported LINE layout.');
  check();
  await api.call('click',{...elementTarget(target,state,detach),delivery_mode:'foreground'});
  await snapshot(api,target);
  for(let attempt=0;attempt<5;attempt++) {
    check();
    inspected=await inspectDetachedChat(api,chatName);
    if(inspected) return inspected;
    await pause(200);
  }
  fail('LINE_CHAT_UNVERIFIED','The opened window does not have the exact requested chat title.');
}

export async function sendPlainText(ui,{chatName,message,chatType,autoSend,idempotencyKey,expectedChatRef,expectedOwnSenderRef}, {
  readMessages, readRecord = loadRecord, writeRecord = saveRecord,
} = {}) {
  const startedAt=Date.now(), deadline=startedAt+30000;
  if(expectedChatRef!==undefined && !/^chat:[0-9a-f]{24}$/u.test(expectedChatRef)) fail('LINE_INVALID_ARGUMENT','Invalid expected chat reference.');
  if(expectedOwnSenderRef!==undefined && !/^sender:[0-9a-f]{24}$/u.test(expectedOwnSenderRef)) fail('LINE_INVALID_ARGUMENT','Invalid expected sender reference.');
  const pairedDirect=hasBoundDirectRefs({chatType,expectedChatRef,expectedOwnSenderRef});
  readMessages ??= pairedDirect ? readLocalLineBoundDirectMessages : readLocalLineMessages;
  const check=()=>{if(Date.now()>=deadline) fail('LINE_SEND_TIMEOUT','LINE text operation reached its deadline.');};
  const read=scope=>{check();return readMessages(scope,{runProcess:s=>runReaderProcess(s,{timeoutMs:Math.max(1,Math.min(5000,deadline-Date.now()))})});};
  const scope=pairedDirect ? boundDirectScope({chatName,chatType,expectedChatRef,expectedOwnSenderRef},startedAt)
    : {chatName,chatType,dateFrom:day(startedAt),dateTo:day(startedAt),messageLimit:50,mediaMode:'metadata'};
  // Resolve both kinds to avoid a detached same-title window identifying another chat.
  const before=await read({...scope,...(pairedDirect?{}:{requireUniqueName:true}),
    ...(expectedChatRef===undefined?{}:{expectedChatRef}),
    ...(expectedOwnSenderRef===undefined?{}:{expectedOwnSenderRef})});
  if(expectedChatRef!==undefined && before.chatRef!==expectedChatRef) fail('CHAT_IDENTITY_CHANGED','The selected chat changed before LINE input.');
  if(expectedOwnSenderRef!==undefined && before.ownSenderRef!==expectedOwnSenderRef) fail('CHAT_ACCOUNT_CHANGED','The selected LINE account changed before LINE input.');
  if(before.chatIdentity.kind!==chatType) fail('CHAT_TYPE_MISMATCH','The requested chat type differs from the local chat.');
  if(!/^sender:[0-9a-f]{24}$/u.test(before.ownSenderRef??'')) fail('LINE_SELF_UNAVAILABLE','LINE did not identify the current sender.');
  if(pairedDirect && (before.chatIdentity.knownNameUnique!==true
    || typeof before.chatIdentity.globalNameUnique!=='boolean'))fail('LINE_CHAT_IDENTITY_UNVERIFIED','Existing direct identity was not verified.');
  const needsContext=pairedDirect && !before.chatIdentity.globalNameUnique;
  const boundScope={...scope,expectedChatRef:before.chatRef,expectedOwnSenderRef:before.ownSenderRef};
  const currentReceiptScope=olderScope=>pairedDirect
    ? boundDirectScope({chatName,chatType,expectedChatRef:before.chatRef,expectedOwnSenderRef:before.ownSenderRef})
    : {...olderScope,expectedChatRef:before.chatRef,expectedOwnSenderRef:before.ownSenderRef,dateTo:day(Date.now())};
  const file=path.join(journalRoot(),`${digest(`${before.ownSenderRef}|${before.chatRef}|${idempotencyKey??message}`)}.json`);
  const old=autoSend?await readRecord(file):null;
  if(old && old.messageDigest!==digest(message)) fail('LINE_SEND_KEY_CONFLICT','This send key belongs to different text.');
  const receiptResult=(record,found,reused=false)=>({success:true,status:'RECORDED_LOCAL',operationId:record.operationId,
    chatName,chatType,sendDispatched:Object.hasOwn(record,'sendDispatched') ? record.sendDispatched : ['RETURN_INTENT','UNCERTAIN','RECORDED_LOCAL'].includes(record.status),deliveryVerified:false,localRecordVerified:true,reused,
    sourceRef:found.sourceRef,sourceMessageId:found.sourceMessageId,sourceTimestamp:found.sourceTimestamp,
    sourceStatus:found.sourceStatus,elapsedMs:Date.now()-startedAt});
  // The same intent never silently becomes a new send after an arbitrary timeout.
  // A deliberately repeated identical message uses a new idempotency key.
  if(old?.status==='RECORDED_LOCAL') return receiptResult(old,old.receipt,true);
  if(old && !['NOT_SENT','RECORDED_LOCAL'].includes(old.status)) {
    const after=old.scope.dateFrom===scope.dateFrom ? before : await read(currentReceiptScope(old.scope));
    const found=matchReceipt(old.before,after,message,old.startedAt);
    if(found){
      if(!Object.hasOwn(old,'sendDispatched')) old.sendDispatched = ['RETURN_INTENT','UNCERTAIN'].includes(old.status);
      old.status='RECORDED_LOCAL';old.receipt=receiptFields(found);
      let journalPersisted=true;
      try { await writeRecord(file,old); } catch { journalPersisted=false; }
      return {...receiptResult(old,found,true),journalPersisted};
    }
    if(['DRAFTING','DRAFTED'].includes(old.status)) throw new LineToolError('LINE_DRAFT_PENDING',
      'The earlier operation stopped before Return. Inspect its draft; no new input was attempted.',
      {operationMayHaveCompleted:false,sendDispatched:false,status:'DRAFTED',draftMayBeStaged:true,operationId:old.operationId});
    throw new LineToolError('LINE_SEND_UNCERTAIN','An earlier operation may have sent this message. No new input was attempted.',
      {operationMayHaveCompleted:true,sendDispatched:null,status:'UNCERTAIN',operationId:old.operationId});
  }
  const record={operationId:randomUUID(),startedAt,messageDigest:digest(message),scope:boundScope,status:'NOT_SENT',
    before:{chatRef:before.chatRef,ownSenderRef:before.ownSenderRef,messages:before.messages.map(({sourceRef,sourceTimestamp})=>({sourceRef,sourceTimestamp}))}};
  let draftAttempted=false,returnAttempted=false,writingDraft=false;
  let uiError;
  try {
    try { await ui.withClient(async rawApi=>{
      const checkInput=()=>{check();if(Date.now()>=deadline-5000) fail('LINE_SEND_TIMEOUT','LINE input deadline reached; time reserved for local receipt verification.');};
      const api={...rawApi,call(name,args){
        checkInput();
        if(writingDraft && name==='set_value') draftAttempted=true;
        if(name==='press_key' && args.key==='return') returnAttempted=true;
        return rawApi.call(name,args);
      }};
      const context=needsContext ? await inspectBoundDirect(ui,api,boundScope,before,()=>read(boundScope),checkInput) : null;
      const inspected=context?.inspected ?? await openExactChat(ui,api,chatName,chatType,check);
      const guard=context?.guard ?? createChatGuard(chatName,inspected,ui.ocr,ui.visual);
      const options=context?.options ?? composerOptions(inspected,guard);
      if(chatType==='group' || (pairedDirect && !needsContext)){
        const current=await read({...boundScope,...(chatType==='group'?{requireUniqueName:true}:{})});
        if(current.chatRef!==before.chatRef || current.ownSenderRef!==before.ownSenderRef
          || current.chatIdentity?.kind!==chatType
          || (pairedDirect && current.chatIdentity?.globalNameUnique!==true))
          fail('LINE_CHAT_IDENTITY_CHANGED','Identity changed before draft input.');
      }
      if(autoSend){record.status='DRAFTING';await writeRecord(file,record);}
      writingDraft=true;
      await writeDraft(api,inspected.target,message,'',options);
       writingDraft=false;
       if(!autoSend)return;
       // The named DB target and signed-in sender can change while LINE opens
       // or while a draft is staged. Recheck before the fresh pre-Return UI guard.
       const current=await read({...boundScope,...(pairedDirect?{}:{requireUniqueName:true})});
       if(current.chatRef!==before.chatRef || current.chatIdentity?.kind!==chatType
         || current.ownSenderRef!==before.ownSenderRef
         || (pairedDirect && !needsContext && current.chatIdentity?.globalNameUnique!==true))
         fail('LINE_CHAT_IDENTITY_CHANGED','The local chat or sender changed before Return.');
       await runUiInput(api,inspected.target,async state=>{
        const composer=findComposer(state,options);
        if(composer.value!==message) fail('LINE_DRAFT_CHANGED','The complete draft changed before Return.');
        checkInput();record.status='RETURN_INTENT';await writeRecord(file,record);checkInput();
        return {element:composer.element};
      },'press_key',{key:'return'},{guard,postGuard:context?.titleGuard ?? guard,deliveryMode:'foreground'});
    },{deadline:deadline-5000});
    } catch(error) {
      if(!returnAttempted) throw error;
      // A post-Return UI/connection failure cannot erase a successful DB receipt.
      uiError=error;
    }
    if(!autoSend)return {success:true,status:'DRAFTED',chatName,chatType,staged:true,sendDispatched:false,deliveryVerified:false,elapsedMs:Date.now()-startedAt};
    for(let attempt=0;attempt<4;attempt++) {
      const after=await read(currentReceiptScope(boundScope));
      const found=matchReceipt(record.before,after,message,startedAt);
      if(found){
        record.status='RECORDED_LOCAL';record.sendDispatched=true;record.receipt=receiptFields(found);
        let journalPersisted=true;
        try { await writeRecord(file,record); } catch { journalPersisted=false; }
        return {...receiptResult(record,found),journalPersisted};
      }
      if(attempt<3)await pause([250,500,1000][attempt]);
    }
    throw uiError ?? new Error('No unique new local record was found after Return.');
  }catch(error){
    record.status=returnAttempted?'UNCERTAIN':draftAttempted?'DRAFTED':'NOT_SENT';
    record.sendDispatched=returnAttempted?null:false;
    let journalPersisted=true;
    if(autoSend)try { await writeRecord(file,record); } catch { journalPersisted=false; }
    throw new LineToolError(error.code??(returnAttempted?'LINE_SEND_UNCERTAIN':'LINE_SEND_FAILED'),error.message,{status:record.status,
      operationId:record.operationId,sendDispatched:returnAttempted?null:false,draftMayBeStaged:draftAttempted,journalPersisted,
      operationMayHaveCompleted:returnAttempted,elapsedMs:Date.now()-startedAt});
  }
}

function receiptFields({sourceRef,sourceMessageId,sourceTimestamp,sourceStatus}) {
  return {sourceRef,sourceMessageId,sourceTimestamp,sourceStatus};
}
