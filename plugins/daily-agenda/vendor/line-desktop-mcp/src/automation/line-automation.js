import { execSync } from 'child_process';
import fs from 'fs';
import path from 'path';
import { MacOSLineAutomation } from './macos-line-automation.js';
import { WindowsLineAutomation } from './windows-line-automation.js';
import { withLineOperation } from './line-operation-lock.mjs';
import { LineUi } from '../extensions/line-ui.mjs';
import { LineToolError } from '../extensions/line-runtime.mjs';
// CODEX_LINE_WORKFLOW_GUARDS_V1

function requireVerifiedChatPlatform(platform) {
  if (platform === 'win32') return;
  throw new LineToolError(
    'LINE_CHAT_VERIFICATION_UNAVAILABLE',
    'Chat-scoped LINE actions are unavailable because this platform cannot verify the exact active chat.',
    { operationMayHaveCompleted: false },
  );
}

function legacySuccess(result) {
  return result?.success === true ? { success: true, error: null } : result;
}

function logVerifiedChatHistory(chatName, chatHistory) {
  if (process.env.CHAT_LOG_ON !== 'true') return;
  try {
    const timestamp = new Date().toISOString().replace(/[:.]/g, '-');
    // Remove only filesystem-unsafe characters, preserve CJK characters.
    const safeChatName = chatName.replace(/[<>:"/\\|?*\x00-\x1f\x7f]/g, '_');
    const fileName = `${safeChatName}_${timestamp}.txt`;
    const logDir = process.env.CHAT_LOG_PATH || path.join(process.cwd(), 'logs');
    if (!fs.existsSync(logDir)) fs.mkdirSync(logDir, { recursive: true });
    const logFilePath = path.join(logDir, fileName);
    fs.writeFileSync(logFilePath, chatHistory);
    console.error(`Chat history saved to ${logFilePath}`);
  } catch (error) {
    console.error('Failed to write chat history to log file:', error);
  }
}

export class LineAutomation {
  constructor() {
    this.platform = process.platform;
        
    if (this.platform === 'darwin') {
      this.automation = new MacOSLineAutomation();
    } else if (this.platform === 'win32') {
      this.automation = new WindowsLineAutomation();
    } else {
      throw new Error(`Unsupported platform: ${this.platform}`);
    }
  }

  async switchToEnglish() {
    return await this.automation.switchToEnglish();
  }

  async selectChat(chatName) {
    return await this.automation.selectChat(chatName);
  }

  async copyAllChatToClipboard() {
    return await this.automation.copyAllChatToClipboard();
  }

  async pageUp(times = 2) {
    return await this.automation.pageUp(times);
  }

  async runOperation(kind, action) {
    return this.platform === 'win32' ? withLineOperation(kind, action) : action();
  }

  getVerifiedUi() {
    this._verifiedUi ??= new LineUi({
      automation: this,
      runOperation: (kind, action) => this.runOperation(kind, action),
    });
    return this._verifiedUi;
  }

  async getChatHistory(chatName, date, messageLimit = 100, pageUpTimes = 10) {
    requireVerifiedChatPlatform(this.platform);
    const history = await this.getVerifiedUi().readLegacyHistory({ chatName, pageUpTimes });
    if (typeof history !== 'string' || !history.trim() || history.trim().startsWith('ERROR:')) {
      throw new LineToolError(
        'HISTORY_READ_FAILED',
        'HISTORY_READ_FAILED: LINE did not return chat text. Do not treat this as empty history or retry blindly.',
      );
    }
    logVerifiedChatHistory(chatName, history);
    return history;
  }

  async sendChatMessage(chatName, message, autoSend = false) {
    requireVerifiedChatPlatform(this.platform);
    const result = await this.getVerifiedUi().sendText({ chatName, message, autoSend });
    return legacySuccess(result);
  }

  async stageFileManual(chatName, filePath, optionalMessage = '') {
    requireVerifiedChatPlatform(this.platform);
    const result = await this.getVerifiedUi().stageFile({ chatName, filePath, optionalMessage });
    return legacySuccess(result);
  }

  async getChatList(includeGroups = true, includeIndividual = true) {
    return await this.automation.getChatList(includeGroups, includeIndividual);
  }

  async isLineRunning() {
    return await this.automation.isLineRunning();
  }

  async activateLine(verifiedTarget) {
    return await this.automation.activateLine(verifiedTarget);
  }
}
