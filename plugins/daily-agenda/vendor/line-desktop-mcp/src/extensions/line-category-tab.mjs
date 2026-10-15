const frame = element => element?.frame && {
  x: element.frame.x, y: element.frame.y,
  width: element.frame.w, height: element.frame.h,
};

const area = box => box.width * box.height;
const contains = (outer, inner) => outer.x <= inner.x && outer.y <= inner.y
  && outer.x + outer.width >= inner.x + inner.width
  && outer.y + outer.height >= inner.y + inner.height;

/** Bind exact OCR words to the current small UIA navigation tab. An OCR line
 * may join adjacent tabs (for example, "全 部 好 友"); its word boxes may not.
 * Failure or distinct matching tabs remain ambiguous and do not authorize a click.
 */
export function exactCategoryTab(recognized, elements, bounds, dimensions, chatType) {
  const category = chatType === 'group' ? '群組' : chatType === 'direct' ? '好友' : null;
  if (!category || !recognized?.lines || !Array.isArray(elements)
    || !bounds || !dimensions?.width || !dimensions?.height) return null;
  const words = recognized.lines.flatMap(line => line.y < 50 && Array.isArray(line.words)
    ? line.words.filter(word => word && typeof word.text === 'string'
      && [word.x, word.y, word.width, word.height].every(Number.isFinite)
      && word.width > 0 && word.height > 0 && word.y < 50) : []);
  if (!words.length) return null;
  const sx = bounds.width / dimensions.width, sy = bounds.height / dimensions.height;
  const wordBox = word => ({x:bounds.x + word.x * sx, y:bounds.y + word.y * sy,
    width:word.width * sx, height:word.height * sy});
  const inTab = (word, tab) => {
    const box = wordBox(word), cx = box.x + box.width / 2, cy = box.y + box.height / 2;
    return cx >= tab.x && cx <= tab.x + tab.width
      && cy >= tab.y && cy <= tab.y + tab.height
      && box.x >= tab.x - 2 && box.x + box.width <= tab.x + tab.width + 2
      && box.y >= tab.y - 2 && box.y + box.height <= tab.y + tab.height + 2;
  };
  const matches = elements.filter(element => {
    const tab = frame(element);
    if (element.role !== 'Group' || !tab || tab.width < 20 || tab.width >= 90
      || tab.height < 20 || tab.height >= 45 || tab.x < bounds.x
      || tab.y < bounds.y || tab.y >= bounds.y + 60) return false;
    const text = words.filter(word => inTab(word, tab))
      .sort((a,b) => a.x - b.x).map(word => word.text.replace(/\s/gu, '')).join('');
    return text === category;
  });
  // The nested Qt Group wrappers can describe one tab. Choose the innermost
  // one, but never pick arbitrarily between separate exact category tabs.
  const leaves = matches.filter(element => !matches.some(other => {
    if (other === element) return false;
    const inner = frame(other), outer = frame(element);
    return area(inner) < area(outer) && contains(outer, inner);
  }));
  return leaves.length === 1 ? leaves[0] : null;
}
