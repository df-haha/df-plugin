import test from 'node:test';
import assert from 'node:assert/strict';

import { exactSearchResult, singleGroupSearchCandidate, singleDirectSearchCandidate,
  mixedDirectSearchCandidate } from '../src/extensions/line-exact-search.mjs';
import { inspectDetachedChat } from '../src/extensions/line-ui.mjs';

const window = { bounds: { x: 0, y: 0, width: 740, height: 600 } };
const dimensions = { width: 738, height: 598 };
const element = (element_index, role, parent_index, x, y, w, h, extra = {}) => ({
  element_index, role, parent_index, frame: { x, y, w, h }, ...extra,
});
function fixture(names = ['Prefix (Example)', 'Example'], categoryCount = names.length) {
  const state = { snapshot_id: 'search', screenshot_width: 738, screenshot_height: 598,
    elements_complete: true, elements: [
      element(0, 'Window', undefined, 0, 0, 740, 600),
      element(1, 'Edit', 0, 10, 60, 300, 40, { value: 'Example' }),
      element(2, 'List', 0, 0, 100, 320, 330),
      element(3, 'ListItem', 2, 0, 100, 320, 35),
      ...names.map((_, index) => element(4 + index, 'ListItem', 2,
        0, 135 + index * 65, 320, 65)),
    ] };
  state.total_element_count = state.elements.length;
  state.returned_element_count = state.elements.length;
  const ocr = { coordinateSpace: 'input-png-pixels', scaleFactor: 1,
    width: 738, height: 598, lines: [
      { text: `好友 (${categoryCount})`, x: 10, y: 109, width: 70, height: 17 },
      ...names.filter(name => name !== null).map((name, index) => ({
        text: name, x: 90, y: 149 + index * 65, width: 120, height: 18,
      })),
    ] };
  return { state, ocr };
}

test('chooses the second result only when its complete title is the unique exact name', () => {
  const { state, ocr } = fixture();
  assert.equal(exactSearchResult(state, window, dimensions, ocr, 'Example', 'direct').element_index, 5);
});

test('prefix and substring results do not count as the exact whole name', () => {
  const { state, ocr } = fixture(['Example (Prefix)', 'Prefix Example']);
  assert.throws(() => exactSearchResult(state, window, dimensions, ocr, 'Example', 'direct'),
    { code: 'LINE_SEARCH_NOT_UNIQUE' });
});

test('multiple exact names and unreadable rows refuse selection', () => {
  const duplicates = fixture(['Example', 'Example']);
  assert.throws(() => exactSearchResult(duplicates.state, window, dimensions,
    duplicates.ocr, 'Example', 'direct'), { code: 'LINE_SEARCH_NOT_UNIQUE' });
  const unreadable = fixture(['Prefix (Example)', null]);
  assert.throws(() => exactSearchResult(unreadable.state, window, dimensions,
    unreadable.ocr, 'Example', 'direct'), { code: 'LINE_SEARCH_NOT_UNIQUE' });
});

test('incomplete result counts or mismatched accessibility element counts refuse selection', () => {
  const countMismatch = fixture(['Prefix (Example)', 'Example'], 3);
  assert.throws(() => exactSearchResult(countMismatch.state, window, dimensions,
    countMismatch.ocr, 'Example', 'direct'), { code: 'LINE_SEARCH_NOT_UNIQUE' });
  const incomplete = fixture();
  incomplete.state.elements_complete = false;
  incomplete.state.total_element_count = incomplete.state.elements.length + 1;
  incomplete.state.returned_element_count = incomplete.state.elements.length;
  assert.throws(() => exactSearchResult(incomplete.state, window, dimensions,
    incomplete.ocr, 'Example', 'direct'), { code: 'LINE_SEARCH_NOT_UNIQUE' });
});

test('ellipsis and a title touching the row edge are not complete-name proof', () => {
  const ellipsis = fixture(['Prefix (Example)', 'Example…']);
  assert.throws(() => exactSearchResult(ellipsis.state, window, dimensions,
    ellipsis.ocr, 'Example', 'direct'), { code: 'LINE_SEARCH_NOT_UNIQUE' });
  const clipped = fixture();
  clipped.ocr.lines[2].x = 260;
  clipped.ocr.lines[2].width = 54;
  assert.throws(() => exactSearchResult(clipped.state, window, dimensions,
    clipped.ocr, 'Example', 'direct'), { code: 'LINE_SEARCH_NOT_UNIQUE' });
});

test('different whitespace, Han spacing, Unicode form and punctuation are not exact names', () => {
  for (const [requested, observed] of [
    ['A B', 'A  B'], ['範例', '範 例'], ['Café', 'Cafe\u0301'], ['Example', 'Example.'],
  ]) {
    const sample = fixture([`Prefix (${requested})`, observed]);
    sample.state.elements.find(item => item.role === 'Edit').value = requested;
    assert.throws(() => exactSearchResult(sample.state, window, dimensions,
      sample.ocr, requested, 'direct'), { code: 'LINE_SEARCH_NOT_UNIQUE' });
  }
});

test('partial window geometry can select the second exact row when OCR proves the full result set', () => {
  const sample = fixture();
  sample.state.elements_complete = false;
  assert.equal(exactSearchResult(sample.state, window, dimensions,
    sample.ocr, 'Example', 'direct').element_index, 5);
});

test('one clipped group result is a navigation candidate without OCR identity claims', () => {
  const name = '【合成測試專案】很長的群組名稱';
  const sample = fixture(['【合成測試專案】很長… (5)']);
  sample.state.elements.find(item => item.role === 'Edit').value = name;
  sample.ocr.lines[0].text = '聊天 1';
  assert.throws(() => exactSearchResult(sample.state, window, dimensions,
    sample.ocr, name, 'group'), { code: 'LINE_SEARCH_NOT_UNIQUE' });
  assert.equal(singleGroupSearchCandidate(sample.state, window, dimensions, name).element_index, 4);
});

test('group navigation refuses changed query, multiple results and incomplete geometry', () => {
  const changed = fixture(['Example']);
  changed.state.elements.find(item => item.role === 'Edit').value = 'Different';
  const incomplete = fixture(['Example']);
  incomplete.state.total_element_count++;
  const outside = fixture(['Example']);
  outside.state.elements.at(-1).frame.y = 500;
  for (const sample of [changed, fixture(), incomplete, outside, fixture([])]) {
    assert.throws(() => singleGroupSearchCandidate(sample.state, window, dimensions, 'Example'),
      { code: 'LINE_SEARCH_NOT_UNIQUE' });
  }
});

function observedDirectSearch() {
  // Header/list geometry from the 2026-09-25 local observation, with a
  // synthetic query and no chat content. OCR omitted the result title.
  const requested = '測試對象♋️';
  const observedWindow = { bounds: { x: 1050, y: 19, width: 1100, height: 1400 } };
  const observedDimensions = { width: 1098, height: 1398 };
  const elements = [
    element(0,'Window',undefined,1050,19,1100,1400),
    element(15,'Group',12,1170,30,34,35),
    element(16,'Group',15,1170,34,28,31),
    element(17,'Group',16,1170,63,26,2),
    element(57,'List',56,1112,116,302,1223),
    element(58,'ListItem',57,1112,116,302,34),
    element(59,'ListItem',57,1112,150,302,71),
    element(61,'Edit',60,1124,82,264,38,{value:requested}),
  ];
  const state={elements,elements_complete:true,total_element_count:elements.length,
    returned_element_count:elements.length,screenshot_width:1098,screenshot_height:1398};
  const ocr={coordinateSpace:'input-png-pixels',scaleFactor:1,width:1098,height:1398,lines:[
    {text:'好 友',x:119,y:16.5,width:25.5,height:12.5,words:[
      {text:'好',x:119,y:16.5,width:12.5,height:12.5},
      {text:'友',x:132,y:16.5,width:12.5,height:12.5}]},
    {text:'聊 1',x:79.5,y:114.5,width:31,height:11.5,words:[
      {text:'聊',x:79.5,y:114.5,width:10.5,height:11.5},
      {text:'1',x:106.5,y:116.5,width:4,height:8.5}]},
    {text:'通話 02:15',x:162.5,y:167.5,width:83.5,height:11.5},
  ]};
  return {state,ocr,requested,observedWindow,observedDimensions};
}

test('one direct result with unreadable title is navigation-only after friend-tab and count proof', () => {
  const sample=observedDirectSearch();
  const {state,observedWindow:win,observedDimensions:dim,ocr,requested}=sample;
  assert.throws(() => exactSearchResult(state,win,dim,ocr,requested,'direct'),
    {code:'LINE_SEARCH_NOT_UNIQUE'});
  assert.equal(singleDirectSearchCandidate(state,win,dim,ocr,requested).element_index,59);
});

test('direct navigation refuses wrong query, multiple rows, incomplete snapshots and inactive friend tab', () => {
  const mutations=[
    sample=>{sample.state.elements.at(-1).value='另一個對象';},
    sample=>{sample.state.elements.push(element(60,'ListItem',57,1112,221,302,71));
      sample.state.total_element_count++;sample.state.returned_element_count++;},
    sample=>{sample.state.total_element_count++;},
    sample=>{sample.state.elements=sample.state.elements.filter(item=>item.element_index!==17);
      sample.state.total_element_count--;sample.state.returned_element_count--;},
  ];
  for(const mutate of mutations){
    const sample=observedDirectSearch();mutate(sample);
    assert.throws(()=>singleDirectSearchCandidate(sample.state,sample.observedWindow,
      sample.observedDimensions,sample.ocr,sample.requested),{code:'LINE_SEARCH_NOT_UNIQUE'});
  }
});

test('direct navigation refuses missing, ambiguous or wrong count category OCR', () => {
  const mutations=[
    sample=>{sample.ocr.lines.splice(1,1);},
    sample=>{sample.ocr.lines.splice(1,0,{...sample.ocr.lines[1]});},
    sample=>{sample.ocr.lines[1].text='聊 2';},
  ];
  for(const mutate of mutations){
    const sample=observedDirectSearch();mutate(sample);
    assert.throws(()=>singleDirectSearchCandidate(sample.state,sample.observedWindow,
      sample.observedDimensions,sample.ocr,sample.requested),{code:'LINE_SEARCH_NOT_UNIQUE'});
  }
});

test('a candidate cannot pass the mandatory detached full-title check with a partial title', async () => {
  const requested='測試對象♋️';
  const api={call:()=>{throw new Error('A wrong titled window must not be inspected.')}};
  const wrong=[{app_name:'LINE.exe',is_on_screen:true,minimized:false,
    pid:42,window_id:99,title:'測試對象'}];
  assert.equal(await inspectDetachedChat(api,requested,{lineWindows:wrong}),undefined);
});

function mixedDirectSearch() {
  const sample=observedDirectSearch();
  sample.requested='Synthetic Chat';
  sample.state.elements.find(item=>item.role==='Edit').value=sample.requested;
  sample.state.elements.push(element(62,'ListItem',57,1112,221,302,34));
  for(let index=0;index<4;index++)
    sample.state.elements.push(element(63+index,'ListItem',57,1112,255+index*71,302,71));
  sample.state.total_element_count=sample.state.elements.length;
  sample.state.returned_element_count=sample.state.elements.length;
  sample.ocr.lines.push(
    {text:'Synthetic Chat',x:155,y:143,width:119,height:17},
    {text:'訊 4',x:80,y:207,width:34,height:14});
  return sample;
}

test('mixed Friends results select only the one exact complete chat above four message hits',()=>{
  const sample=mixedDirectSearch();
  assert.equal(mixedDirectSearchCandidate(sample.state,sample.observedWindow,
    sample.observedDimensions,sample.ocr,sample.requested).element_index,59);
});

test('mixed direct search refuses two chat rows, wrong counts, unreadable headers and stale query',()=>{
  const mutations=[
    sample=>{sample.state.elements.splice(sample.state.elements.findIndex(e=>e.element_index===62),0,
      element(70,'ListItem',57,1112,221,302,71));},
    sample=>{sample.ocr.lines.at(-1).text='訊 3';},
    sample=>{sample.ocr.lines.pop();},
    sample=>{sample.ocr.lines.at(-1).text='未知 4';},
    sample=>{sample.state.elements.find(e=>e.role==='Edit').value='Stale Chat';},
    sample=>{sample.state.total_element_count++;},
    sample=>{sample.state.elements.push(element(71,'ListItem',57,1112,539,302,34));},
  ];
  for(const mutate of mutations) {
    const sample=mixedDirectSearch(); mutate(sample);
    sample.state.total_element_count=sample.state.elements.length
      + (sample.state.total_element_count>sample.state.elements.length?1:0);
    sample.state.returned_element_count=sample.state.elements.length;
    assert.throws(()=>mixedDirectSearchCandidate(sample.state,sample.observedWindow,
      sample.observedDimensions,sample.ocr,sample.requested),{code:'LINE_SEARCH_NOT_UNIQUE'});
  }
});

test('mixed direct search needs the selected tab and exact untruncated full name',()=>{
  const mutations=[
    sample=>{sample.state.elements=sample.state.elements.filter(e=>e.element_index!==17);},
    sample=>{sample.ocr.lines.find(e=>e.text==='Synthetic Chat').text='SyntheticChat';},
    sample=>{sample.ocr.lines.find(e=>e.text==='Synthetic Chat').text='Synthetic Chat…';},
    sample=>{sample.ocr.lines.find(e=>e.text==='Synthetic Chat').x=310;},
    sample=>{sample.ocr.lines.find(e=>e.text==='Synthetic Chat').text='Synthetic  Chat';},
  ];
  for(const mutate of mutations) {
    const sample=mixedDirectSearch(); mutate(sample);
    sample.state.total_element_count=sample.state.elements.length;
    sample.state.returned_element_count=sample.state.elements.length;
    assert.throws(()=>mixedDirectSearchCandidate(sample.state,sample.observedWindow,
      sample.observedDimensions,sample.ocr,sample.requested),{code:'LINE_SEARCH_NOT_UNIQUE'});
  }
});
