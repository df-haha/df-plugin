/**
 * Pure logic for the PreToolUse guard hook.
 * No I/O, no process.exit — testable as a module.
 *
 * LINE send-class tools matched:
 *   - send_message_auto, send_message_manual, send_file_manual
 *   - stage_line_reply, stage_line_forward
 *   - prepare_line_forward, confirm_line_forward
 *   - Any mcp__*line*__* tool containing send/reply/forward in tool name
 *
 * Explicitly allowed (never blocked):
 *   - mcp__plugin_daily-agenda_actions__* (our own proxy path)
 */

/** Tool names that are LINE send-class (non-MCP or legacy tool names). */
const LINE_SEND_TOOL_NAMES = new Set([
  'send_message_auto',
  'send_message_manual',
  'send_file_manual',
  'stage_line_reply',
  'stage_line_forward',
  'prepare_line_forward',
  'confirm_line_forward',
]);

/** Our own proxy path — always allowed. */
const PROXY_PREFIX = 'mcp__plugin_daily-agenda_actions__';

/**
 * Check if a tool name is a LINE send-class tool that should be blocked.
 * @param {string} toolName
 * @returns {boolean}
 */
export const isBlockedLineTool = (toolName) => {
  if (typeof toolName !== 'string') return false;

  // Our own proxy path is always allowed
  if (toolName.startsWith(PROXY_PREFIX)) return false;

  // Direct LINE send tool names
  if (LINE_SEND_TOOL_NAMES.has(toolName)) return true;

  // MCP tools from LINE-related servers
  if (!toolName.startsWith('mcp__')) return false;

  // Parse: mcp__<server_name>__<tool_name>
  const parts = toolName.split('__');
  if (parts.length < 3) return false;

  const serverName = parts[1];
  const mcpToolName = parts.slice(2).join('__');

  // Check if server name contains "line" as a word boundary token
  // (not "timeline", "pipeline", "online")
  const serverTokens = serverName.split(/[-_]/);
  const hasLineToken = serverTokens.some((t) => t.toLowerCase() === 'line');
  if (!hasLineToken) return false;

  // Check if tool name is send-class (must START with send_ or stage_, or be
  // a specific forward/reply tool, not just contain "send" — check_line_send_target
  // and prepare_line_send_target are read-only identity checks, not send actions)
  const toolLower = mcpToolName.toLowerCase();
  if (toolLower.startsWith('send_')) return true;
  if (toolLower.startsWith('stage_')) return true;
  if (toolLower === 'prepare_line_forward' || toolLower === 'confirm_line_forward') return true;

  return false;
};
