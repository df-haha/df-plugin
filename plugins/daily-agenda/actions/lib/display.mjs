/**
 * Derive the display strings that commit_action must match exactly.
 * A single function guarantees prepare and commit use the same logic.
 */

/**
 * Strip C0/C1 control characters except \n (\x0a) and \t (\x09).
 * Applied at prepare time so what users confirm is what gets stored/sent.
 * @param {string} text
 * @returns {string}
 */
export const sanitizeContent = (text) =>
  text.replace(/[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]/g, '');

/**
 * @param {{kind: string, target: unknown, content: unknown}} payload
 * @returns {{target_display: string, content_display: string}}
 */
export const deriveDisplay = (payload) => {
  if (payload.kind === 'line_reply') {
    return deriveLineReply(payload);
  }
  if (payload.kind === 'calendar_event') {
    return deriveCalendarEvent(payload);
  }
  throw new Error(`Unknown action kind: ${payload.kind}`);
};

/**
 * @param {{target: {chat_ref: string, chat_name: string, chat_type: string}, content: string}} payload
 */
const deriveLineReply = (payload) => {
  const { chat_name, chat_type } = payload.target;
  const targetDisplay = `LINE ${chat_type === 'group' ? '群組' : '私訊'}「${chat_name}」`;
  return {
    target_display: targetDisplay,
    content_display: payload.content,
  };
};

/**
 * @param {{target: {start_iso: string, end_iso: string, subject: string, location?: string, attendees?: string[]}}} payload
 */
const deriveCalendarEvent = (payload) => {
  const t = payload.target;
  const parts = [`${t.subject}`, `${t.start_iso} ~ ${t.end_iso}`];
  if (t.location) parts.push(`地點: ${t.location}`);
  if (t.attendees && t.attendees.length > 0) parts.push(`出席: ${t.attendees.join(', ')}`);
  return {
    target_display: `行事曆事件`,
    content_display: parts.join('\n'),
  };
};
