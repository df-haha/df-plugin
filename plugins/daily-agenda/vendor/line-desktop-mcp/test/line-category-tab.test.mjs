import test from 'node:test';
import assert from 'node:assert/strict';
import { exactCategoryTab } from '../src/extensions/line-category-tab.mjs';

// Header-only values from the 2026-09-25 local LINE observation. The full
// screenshot OCR merges adjacent tabs into one line; no chat content is used.
const bounds = {x:1050, y:19, width:1100, height:1400};
const dimensions = {width:1098, height:1398};
const word = (text,x,y,width,height) => ({text,x,y,width,height});
const mergedHeader = {lines:[
  {text:'全 部 好 友',y:16.5,words:[
    word('全',73.5,16.5,11.5,12.5),word('部',86.5,17,11.5,12),
    word('好',119.5,17,11.5,12),word('友',132.5,17,11.5,11.5)]},
  {text:'群 組',y:16.5,words:[word('群',171,16.5,12.5,12.5),word('組',184,16.5,13,12.5)]},
]};
const group = (element_index, parent_index, x, y, w, h) =>
  ({role:'Group',element_index,parent_index,frame:{x,y,w,h}});
const tabs = [
  group(15,12,1170,30,34,35),group(16,15,1170,34,28,31),
  group(18,12,1222,30,34,35),group(19,18,1222,34,28,31),
];

test('real merged OCR header binds direct and group to distinct innermost UIA tabs', () => {
  assert.equal(exactCategoryTab(mergedHeader,tabs,bounds,dimensions,'direct')?.element_index,16);
  assert.equal(exactCategoryTab(mergedHeader,tabs,bounds,dimensions,'group')?.element_index,19);
});

test('category selection refuses a second distinct exact tab', () => {
  const duplicate = {text:'好 友',y:16.5,words:[
    word('好',419.5,17,11.5,12),word('友',432.5,17,11.5,11.5)]};
  const ocr={lines:[...mergedHeader.lines,duplicate]};
  const elements=[...tabs,group(90,12,1470,30,34,35),group(91,90,1470,34,28,31)];
  assert.equal(exactCategoryTab(ocr,elements,bounds,dimensions,'direct'),null);
});

test('partial or spanning words cannot authorize a category tab', () => {
  const partial={lines:[{text:'好',y:17,words:[word('好',119.5,17,11.5,12)]}]};
  assert.equal(exactCategoryTab(partial,tabs,bounds,dimensions,'direct'),null);
  const spanning={lines:[{text:'全部好友',y:16.5,
    words:[word('全部好友',73.5,16.5,70.5,12.5)]}]};
  assert.equal(exactCategoryTab(spanning,tabs,bounds,dimensions,'direct'),null);
});
