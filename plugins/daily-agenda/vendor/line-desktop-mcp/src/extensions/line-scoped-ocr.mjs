import { LineToolError } from './line-runtime.mjs';
import { fingerprintLineRegion, recognizeLineImage } from './line-ocr.mjs';
import { directSearchLayout } from './line-group-navigation.mjs';
import { mixedDirectSearchStructure, singleGroupSearchCandidate } from './line-exact-search.mjs';

const invalid = () => { throw new LineToolError('LINE_OCR_INVALID_REGION',
  'The current LINE screenshot does not prove a bounded navigation or complete result region.'); };
const frame = element => element?.frame && { x:element.frame.x, y:element.frame.y,
  width:element.frame.w ?? element.frame.width, height:element.frame.h ?? element.frame.height };
const finiteBox = box => box && [box.x,box.y,box.width,box.height].every(Number.isFinite)
  && box.width>0 && box.height>0;
const inside = (inner, outer) => finiteBox(inner) && finiteBox(outer)
  && inner.x>=outer.x && inner.y>=outer.y
  && inner.x+inner.width<=outer.x+outer.width
  && inner.y+inner.height<=outer.y+outer.height;
const same = (a,b) => ['x','y','width','height'].every(key => a?.[key]===b?.[key]);
const pixels = (left, top, right, bottom, dimensions) => {
  const box={x:Math.floor(left),y:Math.floor(top),
    width:Math.ceil(right)-Math.floor(left),height:Math.ceil(bottom)-Math.floor(top)};
  if(!inside(box,{x:0,y:0,width:dimensions.width,height:dimensions.height})
    || !Object.values(box).every(Number.isSafeInteger)) invalid();
  return box;
};

/** The live Qt divider bounds every category tab. The unique search Edit
 * supplies the bottom of the small navigation crop; neither OCR nor an
 * approximate window size is allowed to choose a tab. */
export function navigationOcrRegion(state, window, dimensions) {
  const bounds=window?.bounds;
  if(!finiteBox(bounds) || !finiteBox({x:0,y:0,width:dimensions?.width,height:dimensions?.height})
    || !Array.isArray(state?.elements)) invalid();
  const dividers=state.elements.filter(item => item.role==='Thumb'
    && String(item.label??'').startsWith('qt_splithandle_') && inside(frame(item),bounds)
    && frame(item).x>bounds.x+200 && frame(item).x<bounds.x+bounds.width/2);
  const searches=state.elements.filter(item => item.role==='Edit' && inside(frame(item),bounds)
    && frame(item).x<bounds.x+bounds.width/2 && frame(item).y<bounds.y+160
    && frame(item).width>100 && frame(item).height<70);
  if(dividers.length!==1 || searches.length!==1) invalid();
  const divider=frame(dividers[0]), search=frame(searches[0]);
  if(search.x+search.width>divider.x || search.y<bounds.y+40) invalid();
  const scaleX=dimensions.width/bounds.width, scaleY=dimensions.height/bounds.height;
  if(scaleX<=0 || scaleY<=0 || scaleX>1.01 || scaleY>1.01
    || Math.abs(scaleX-scaleY)>4/Math.min(bounds.width,bounds.height)) invalid();
  const region=pixels(0,0,(divider.x+divider.width-bounds.x)*scaleX,
    (search.y+search.height+30-bounds.y)*scaleY,dimensions);
  if(region.width>=dimensions.width/2 || region.height>160*scaleY) invalid();
  return region;
}

/** Crop every observed ListItem, from the category/count row through the last
 * result row. Existing result validators still require a complete readable
 * count and every title before an exact row may be selected. */
export function searchResultsOcrRegion(state, window, dimensions) {
  const layout=directSearchLayout(state,window,dimensions);
  const {root,scaleX,scaleY,list,category}=layout.geometry;
  const lists=state.elements.filter(item=>item.role==='List' && same(frame(item),list));
  if(lists.length!==1) invalid();
  const rows=state.elements.filter(item=>item.role==='ListItem'
    && item.parent_index===lists[0].element_index);
  if(rows.length<2 || !rows.every(item=>inside(frame(item),list))
    || !rows.some(item=>same(frame(item),category))) invalid();
  const bottom=Math.max(...rows.map(item=>frame(item).y+frame(item).height));
  const region=pixels((list.x-root.x)*scaleX,(category.y-root.y)*scaleY,
    (list.x+list.width-root.x)*scaleX,(bottom-root.y)*scaleY,dimensions);
  if(region.y<50 || !inside(layout.region,region)) invalid();
  return region;
}

/** A single direct result needs the category/count independently of its
 * preview. Whole-row OCR can omit a faint category beside a green title.
 * Structural uniqueness only permits navigation; the caller still verifies
 * the selected Friends tab, count and detached full title. */
export function singleDirectSearchOcrRegion(state, window, dimensions, chatName) {
  try { singleGroupSearchCandidate(state, window, dimensions, chatName); }
  catch { invalid(); }
  const {root,scaleX,scaleY,category}=directSearchLayout(state,window,dimensions).geometry;
  return pixels((category.x-root.x)*scaleX,(category.y-root.y)*scaleY,
    (category.x+category.width-root.x)*scaleX,
    Math.floor((category.y+category.height-root.y)*scaleY),dimensions);
}

/** In a mixed direct search, isolate each section label and the complete
 * title band. Neither previews nor message-hit rows enter these crops. */
export function mixedDirectSearchOcrRegions(state, window, dimensions, chatName) {
  let structure;
  try { structure=mixedDirectSearchStructure(state,window,dimensions,chatName); }
  catch { invalid(); }
  const {layout,chatRow,messageHeader}=structure;
  const {root,scaleX,scaleY,list,category}=layout.geometry;
  const header=frame(messageHeader), chat=frame(chatRow);
  const headers=pixels((list.x-root.x)*scaleX,(category.y-root.y)*scaleY,
    (list.x+list.width-root.x)*scaleX,Math.floor((header.y+header.height-root.y)*scaleY),dimensions);
  const chatHeader=pixels((category.x-root.x)*scaleX,(category.y-root.y)*scaleY,
    (category.x+category.width-root.x)*scaleX,Math.floor((category.y+category.height-root.y)*scaleY),dimensions);
  const messageHeaderRegion=pixels((header.x-root.x)*scaleX,(header.y-root.y)*scaleY,
    (header.x+header.width-root.x)*scaleX,Math.floor((header.y+header.height-root.y)*scaleY),dimensions);
  const title=pixels((chat.x+76-root.x)*scaleX,(chat.y-root.y)*scaleY,
    (chat.x+chat.width-root.x)*scaleX,(chat.y+35-root.y)*scaleY,dimensions);
  if(Math.abs(header.y-chat.y-chat.height)>4 || !inside(title,headers)
    || title.width<60 || title.height<18 || !inside(layout.region,headers)) invalid();
  return {headers,chatHeader,messageHeader:messageHeaderRegion,title,
    messageHeaderTop:Math.floor((header.y-root.y)*scaleY)};
}

/** OCR geometry is returned to original PNG coordinates before the existing
 * exact category/result validators receive it. Text is never normalized. */
export function rebaseScopedOcr(ocr, region, dimensions) {
  if(!finiteBox(region) || !inside(region,{x:0,y:0,width:dimensions?.width,height:dimensions?.height})
    || ocr?.coordinateSpace!=='input-png-pixels' || ocr.scaleFactor!==1
    || ocr.width!==region.width || ocr.height!==region.height || !Array.isArray(ocr.lines)) invalid();
  const move=box=>{
    if(!inside(box,{x:0,y:0,width:region.width,height:region.height})) invalid();
    return {...box,x:box.x+region.x,y:box.y+region.y};
  };
  return {...ocr,width:dimensions.width,height:dimensions.height,
    lines:ocr.lines.map(line=>({...move(line),
      words:Array.isArray(line.words)?line.words.map(move):line.words}))};
}

export async function recognizeScopedLineImage(image, region, dimensions, {
  crop=fingerprintLineRegion, recognize=recognizeLineImage, paddingPixels=0,
}={}) {
  if(!Number.isSafeInteger(paddingPixels) || paddingPixels<0 || paddingPixels>32) invalid();
  const result=await crop(image,region,{includeImage:true});
  if(!result?.image || !same(result.region,region)
    || result.width!==region.width || result.height!==region.height) invalid();
  const ocr=paddingPixels ? await recognize(result.image,{paddingPixels}) : await recognize(result.image);
  return rebaseScopedOcr(ocr,region,dimensions);
}

export function combineNavigationAndResults(nav, results, dimensions) {
  if(nav?.width!==dimensions?.width || nav?.height!==dimensions?.height
    || results?.width!==dimensions.width || results?.height!==dimensions.height
    || nav.coordinateSpace!=='input-png-pixels' || results.coordinateSpace!=='input-png-pixels'
    || nav.scaleFactor!==1 || results.scaleFactor!==1
    || !Array.isArray(nav.lines) || !Array.isArray(results.lines)) invalid();
  if(results.lines.some(line=>line.y<50)) invalid();
  return {...results,lines:[...nav.lines.filter(line=>line.y<50),...results.lines]};
}
