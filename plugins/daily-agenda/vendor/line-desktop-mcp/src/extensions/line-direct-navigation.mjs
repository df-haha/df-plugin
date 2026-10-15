// Navigation only. A selected search row is never an identity or send proof.
import { snapshot, elementTarget, mainLineWindow } from './cua-line-client.mjs';
import { LineToolError } from './line-runtime.mjs';
import { recognizeLineImage, fingerprintLineRegion, purepngDimensions } from './line-ocr.mjs';
import { createHash } from 'node:crypto';

const fail = code => { throw new LineToolError(code, 'Direct-chat navigation stopped. No message was sent.', {sendDispatched:false}); };
const rect = e => e?.frame && { x:e.frame.x,y:e.frame.y,width:e.frame.w??e.frame.width,height:e.frame.h??e.frame.height };
const inside = (r,b) => r && [r.x,r.y,r.width,r.height].every(Number.isFinite)
  && r.width>0 && r.height>0 && r.x>=b.x && r.y>=b.y
  && r.x+r.width<=b.x+b.width+1 && r.y+r.height<=b.y+b.height+1;
const same = (a,b) => a?.pid===b?.pid && a?.window_id===b?.window_id;
const sameWindow = (a,b) => same(a,b) && ['x','y','width','height'].every(k=>a?.bounds?.[k]===b?.bounds?.[k]);
const sleep = ms => new Promise(resolve=>setTimeout(resolve,ms));
const compact = t => t.normalize('NFC').replace(/\s/g,'');

// Qt accessibility can be partial even when all returned rows fit the cap.
// These rows supply positive crop geometry only, never UIA uniqueness proof.
// The separate image observation and local message anchors establish identity.
export function hasDirectGeometrySnapshot(state) {
  if (!Array.isArray(state?.elements) || state.elements.length===0 || state.elements.length>=800 || state.query) return false;
  if (state.elements_complete===false || state.total_element_count!==undefined || state.returned_element_count!==undefined) {
    return Number.isInteger(state.total_element_count)
      && state.total_element_count===state.returned_element_count
      && state.returned_element_count===state.elements.length;
  }
  return true;
}

export function directSearchLayout(state, window, dimensions) {
  if (!hasDirectGeometrySnapshot(state)) fail('LINE_DIRECT_LAYOUT_UNAVAILABLE');
  const b=window.bounds;
  if (!b || ![b.x,b.y,b.width,b.height,dimensions.width,dimensions.height].every(Number.isFinite)
      || Math.abs(dimensions.width-b.width)>6 || Math.abs(dimensions.height-b.height)>6) fail('LINE_DIRECT_LAYOUT_UNAVAILABLE');
  const candidates=state.elements.filter(e=>String(e.role).toLowerCase()==='edit' && rect(e)
    && inside(rect(e),b) && rect(e).x<b.x+b.width/2 && rect(e).y<b.y+Math.min(160,b.height/3)
    && rect(e).width>100 && rect(e).height<70);
  if(candidates.length!==1) fail('LINE_DIRECT_SEARCH_UNAVAILABLE');
  const edit=candidates[0], f=rect(edit);
  const lists=state.elements.filter(e=>String(e.role).toLowerCase()==='list' && rect(e)
    && rect(e).x>=b.x && rect(e).x<b.x+b.width/2 && rect(e).y>=f.y+f.height-8
    && rect(e).x<=f.x && rect(e).x+rect(e).width>=f.x+f.width
    && inside(rect(e),b) && rect(e).width>100 && rect(e).width<b.width*.65);
  if(lists.length!==1) fail('LINE_DIRECT_SEARCH_UNAVAILABLE');
  const list=lists[0], listFrame=rect(list);
  const rows=state.elements.filter(e=>String(e.role).toLowerCase()==='listitem'
    && e.parent_index===list.element_index && rect(e)).sort((a,b)=>rect(a).y-rect(b).y);
  // Two separate rows: the category count and its first result. A model must
  // independently establish category count=1 and the COMPLETE exact title.
  if(rows.length<2 || !inside(rect(rows[0]),listFrame) || !inside(rect(rows[1]),listFrame)
    || rect(rows[0]).height<20 || rect(rows[0]).height>48
    || rect(rows[1]).height<45 || rect(rows[1]).height>110
    || Math.abs(rect(rows[1]).y-(rect(rows[0]).y+rect(rows[0]).height))>4) fail('LINE_DIRECT_SEARCH_UNAVAILABLE');
  const x=Math.max(0,Math.floor(listFrame.x-b.x));
  // The query is separately read back exactly. Its blinking caret must not be
  // part of the stable search-result pixel proof.
  const y=Math.max(0,Math.floor(rect(rows[0]).y-b.y));
  const width=Math.min(Math.ceil(listFrame.width),dimensions.width-x);
  const height=Math.min(Math.ceil(rect(rows[1]).y+rect(rows[1]).height-b.y-y),dimensions.height-y);
  if(width<150 || height<90) fail('LINE_DIRECT_SEARCH_UNAVAILABLE');
  const row=rect(rows[1]);
  // This build animates the 53x53 avatar in the left part of the result row.
  // Bind the complete category/count row and the full right-hand title area;
  // retain the unmasked crop for the caller's independent visual observation.
  const titleX=x+76;
  const titleY=Math.floor(row.y-b.y);
  if(width-76<80 || titleY<y+rect(rows[0]).height || y+height-titleY<40) fail('LINE_DIRECT_SEARCH_UNAVAILABLE');
  return {edit,region:{x,y,width,height},
    categoryRegion:{x,y,width,height:Math.floor(rect(rows[0]).height)},
    titleRegion:{x:titleX,y:titleY,width:width-76,height:y+height-titleY},
    geometry:{edit:rect(edit),list:listFrame,category:rect(rows[0]),result:row},
    rowPoint:{x:row.x-b.x+row.width/2,y:row.y-b.y+row.height/2}};
}

function imageOf(state) {
  if(state.images?.length!==1) fail('LINE_DIRECT_IMAGE_UNAVAILABLE');
  return state.images[0];
}

export async function captureDirectSearch(api, window, chatName, helpers={}) {
  const fp=helpers.fingerprintRegion??fingerprintLineRegion;
  const dimensions=helpers.imageDimensions??purepngDimensions;
  const target={pid:window.pid,window_id:window.window_id};
  const state=await snapshot(api,target,{screenshot:true});
  const image=imageOf(state),layout=directSearchLayout(state,window,dimensions(image));
  if(layout.edit.value!==chatName) fail('LINE_DIRECT_QUERY_CHANGED');
  const crop=await fp(image,layout.region,{includeImage:true});
  const category=await fp(image,layout.categoryRegion,{includeImage:false});
  const title=await fp(image,layout.titleRegion,{includeImage:false});
  const valid=(part,region)=>part?.sha256 && /^[0-9a-f]{64}$/u.test(part.sha256)
    && JSON.stringify(part.region)===JSON.stringify(region)
    && part.width===region.width && part.height===region.height;
  if(!valid(crop,layout.region) || !valid(category,layout.categoryRegion)
    || !valid(title,layout.titleRegion) || !crop.image) fail('LINE_DIRECT_IMAGE_UNAVAILABLE');
  const stableHash=createHash('sha256').update(JSON.stringify({v:1,window:window.bounds,
    region:layout.region,geometry:layout.geometry,rowPoint:layout.rowPoint,
    category:{region:layout.categoryRegion,sha256:category.sha256},
    title:{region:layout.titleRegion,sha256:title.sha256}})).digest('hex');
  return {target,window,searchFingerprint:{sha256:stableHash,region:crop.region,width:crop.width,height:crop.height},
    searchImage:crop.image,rowPoint:layout.rowPoint,capturedAt:new Date().toISOString()};
}

export async function searchDirectCandidate(api, automation, chatName, helpers={}) {
  const recognize=helpers.recognizeImage??recognizeLineImage;
  const fp=helpers.fingerprintRegion??fingerprintLineRegion;
  const dimensions=helpers.imageDimensions??purepngDimensions;
  let window=await mainLineWindow(api);
  const target={pid:window.pid,window_id:window.window_id};
  // Background row clicks were reproducibly no-ops on this verified Qt build.
  // Use the existing exact-window activation routine, then discard old state.
  if((await automation.activateLine({...target,title:window.title}))?.success!==true) fail('LINE_FOCUS_UNAVAILABLE');
  const activated=await mainLineWindow(api);
  if(!same(target,activated) || activated.minimized || !activated.is_on_screen
    || (!window.minimized && !sameWindow(window,activated))) fail('LINE_DIRECT_WINDOW_CHANGED');
  window=activated;
  let state=await snapshot(api,target,{screenshot:true});
  let image=imageOf(state),size=dimensions(image);
  const top=await fp(image,{x:0,y:0,width:size.width,height:Math.min(48,size.height)},{includeImage:true});
  const labels=(await recognize(top.image)).lines.filter(l=>compact(l.text)==='好友' || compact(l.text)==='Friends');
  if(labels.length!==1) fail('LINE_DIRECT_FRIENDS_FILTER_UNAVAILABLE');
  const label=labels[0];
  await api.call('click',{...target,x:label.x+label.width/2,y:label.y+label.height/2,delivery_mode:'foreground'});
  state=await snapshot(api,target,{screenshot:true});
  // Locate only the narrow top-left single-line search Edit. Never the composer.
  const b=window.bounds;
  const edits=state.elements.filter(e=>String(e.role).toLowerCase()==='edit' && rect(e)
    && inside(rect(e),b) && rect(e).x<b.x+b.width/2 && rect(e).y<b.y+Math.min(160,b.height/3)
    && rect(e).width>100 && rect(e).height<70);
  if(edits.length!==1) fail('LINE_DIRECT_SEARCH_UNAVAILABLE');
  await api.call('set_value',{...elementTarget(target,state,edits[0]),value:chatName});
  state=await snapshot(api,target,{screenshot:false});
  if(!state.elements.some(e=>String(e.role).toLowerCase()==='edit' && e.value===chatName && rect(e)?.x<b.x+b.width/2)) fail('LINE_DIRECT_QUERY_CHANGED');
  // Search is asynchronous. Require two equal bounded search crops; no repeated
  // clicks, keystrokes, or sends are performed while waiting for it to settle.
  let prior;
  for(let attempt=0;attempt<5;attempt++) {
    await sleep(450);
    window=await mainLineWindow(api);
    if(!same(target,window)) fail('LINE_DIRECT_WINDOW_CHANGED');
    let current;
    try {current=await captureDirectSearch(api,window,chatName,helpers);} catch(e) {
      if(e.code!=='LINE_DIRECT_SEARCH_UNAVAILABLE') throw e;
      continue;
    }
    if(prior?.searchFingerprint.sha256===current.searchFingerprint.sha256) return current;
    prior=current;
  }
  fail('LINE_DIRECT_SEARCH_UNSETTLED');
}

export async function selectDirectCandidate(api, automation, record, helpers={}) {
  let window=await mainLineWindow(api);
  if(!same(record.target,window) || !sameWindow(record.window,window)) fail('LINE_DIRECT_WINDOW_CHANGED');
  if((await automation.activateLine({...record.target,title:window.title}))?.success!==true) fail('LINE_FOCUS_UNAVAILABLE');
  // Fresh state after activation, with complete query/crop/point comparison.
  window=await mainLineWindow(api);
  if(!same(record.target,window) || !sameWindow(record.window,window)) fail('LINE_DIRECT_WINDOW_CHANGED');
  const fresh=await captureDirectSearch(api,window,record.chatName,helpers);
  if(fresh.searchFingerprint.sha256!==record.searchFingerprint.sha256
    || JSON.stringify(fresh.searchFingerprint.region)!==JSON.stringify(record.searchFingerprint.region)
    || fresh.rowPoint.x!==record.rowPoint.x || fresh.rowPoint.y!==record.rowPoint.y) fail('LINE_DIRECT_SEARCH_CHANGED');
  await api.call('click',{...record.target,...fresh.rowPoint,delivery_mode:'foreground'});
  const state=await snapshot(api,record.target,{screenshot:true});
  return {state,window,target:record.target,capturedAt:new Date().toISOString()};
}
