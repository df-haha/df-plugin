// src/automation/windows-line-automation.js
import { execFile, execFileSync } from 'child_process';
import { promisify } from 'util';
import fs from 'fs/promises';
import path from 'path';
import os from 'os';
import iconv from 'iconv-lite';
import chardet from 'chardet';

const execFileAsync = promisify(execFile);

export function configuredAutoHotkeyPath(value,
  programFiles = process.env.ProgramFiles ?? process.env.PROGRAMFILES) {
  const candidate = value === undefined && typeof programFiles === 'string' && path.isAbsolute(programFiles)
    ? path.join(programFiles, 'AutoHotkey', 'v2', 'AutoHotkey64.exe') : value;
  if (typeof candidate !== 'string' || !candidate || candidate !== candidate.trim()
      || candidate.includes('\0') || !path.isAbsolute(candidate)
      || path.extname(candidate).toLowerCase() !== '.exe') return null;
  return candidate;
}

function validVerifiedLineTarget(target) {
  return target && Number.isSafeInteger(target.window_id) && target.window_id > 0
    && Number.isSafeInteger(target.pid) && target.pid > 0
    && typeof target.title === 'string' && target.title.length > 0
    && !/[\r\n\0]/u.test(target.title);
}

// CODEX_LINE_AHK_UTF8_DECODER_V1
// AHK is configured to emit UTF-8-RAW. Only use charset detection after a
// strict UTF-8 decode has proved that the output is not valid UTF-8.
function decodeAhkOutput(bytes, streamName) {
  const buffer = Buffer.isBuffer(bytes) ? bytes : Buffer.from(bytes ?? '');
  try {
    return new TextDecoder('utf-8', { fatal: true }).decode(buffer);
  } catch (utf8Error) {
    const detectedEncoding = chardet.detect(buffer);
    if (!detectedEncoding) return buffer.toString('utf8');
    try {
      return iconv.decode(buffer, detectedEncoding);
    } catch (conversionError) {
      console.warn(`Failed to decode AHK ${streamName} as ${detectedEncoding}:`, conversionError.message);
      return buffer.toString('utf8');
    }
  }
}


const HISTORY_AHK_GUARDS = String.raw`
; CODEX_LINE_HISTORY_CLICK_GUARDS_V1
CoordMode "Mouse", "Screen"
; This is a geometric/focus guard only. It deliberately does not claim UIA
; semantic certainty for Qt content. The history rail point is intended to avoid
; message/image content; other LINE layouts still need live verification.
LINE_GUARD_FAIL(code) {
  FileAppend "ERROR: " code, "*"
  ExitApp(1)
}

AcquireExactLineTarget(expectedHwnd := 0, expectedTitle := "LINE", expectedPid := 0) {
  DetectHiddenWindows False
  matches := []
  for hwnd in WinGetList("ahk_exe LINE.exe") {
    try {
      if !DllCall("IsWindowVisible", "Ptr", hwnd, "Int")
        continue
      if (expectedHwnd && hwnd != expectedHwnd)
        continue
      if (WinGetTitle("ahk_id " hwnd) != expectedTitle)
        continue
      if !RegExMatch(WinGetClass("ahk_id " hwnd), "^Qt\d+QWindowIcon$")
        continue
      pid := WinGetPID("ahk_id " hwnd)
      if !pid
        continue
      if (expectedPid && pid != expectedPid)
        continue
      matches.Push({ hwnd: hwnd, pid: pid })
    } catch {
      LINE_GUARD_FAIL("LINE_TARGET_QUERY_FAILED")
    }
  }
  if (matches.Length != 1)
    LINE_GUARD_FAIL("LINE_TARGET_NOT_UNIQUE")

  target := matches[1]
  if !DllCall("IsWindowEnabled", "Ptr", target.hwnd, "Int")
    LINE_GUARD_FAIL("LINE_MODAL_OPEN")
  if (expectedHwnd && WinGetMinMax("ahk_id " target.hwnd) = -1) {
    WinRestore "ahk_id " target.hwnd
    if (WinGetMinMax("ahk_id " target.hwnd) = -1)
      LINE_GUARD_FAIL("LINE_RESTORE_FAILED")
  }
  WinActivate "ahk_id " target.hwnd
  if !WinWaitActive("ahk_id " target.hwnd,, 2)
    LINE_GUARD_FAIL("LINE_FOCUS_UNAVAILABLE")
  AssertExactLineFocus(target)
  return target
}

AssertExactLineFocus(target) {
  active := WinExist("A")
  if (!active || active != target.hwnd)
    LINE_GUARD_FAIL("LINE_FOCUS_CHANGED")
  if (WinGetPID("ahk_id " active) != target.pid)
    LINE_GUARD_FAIL("LINE_FOCUS_CHANGED")
}

ReadCurrentLineBounds(target) {
  AssertExactLineFocus(target)
  WinGetPos &x, &y, &w, &h, "ahk_id " target.hwnd
  if (w < 700 || h < 600)
    LINE_GUARD_FAIL("LINE_BOUNDS_UNSAFE")
  return { x: x, y: y, w: w, h: h }
}

SameBounds(left, right) {
  return left.x = right.x && left.y = right.y && left.w = right.w && left.h = right.h
}

RelativeLinePoint(target, offsetX, offsetY) {
  bounds := ReadCurrentLineBounds(target)
  point := { x: bounds.x + offsetX, y: bounds.y + offsetY, bounds: bounds }
  if (point.x <= bounds.x || point.x >= bounds.x + bounds.w - 1 || point.y <= bounds.y || point.y >= bounds.y + bounds.h - 1)
    LINE_GUARD_FAIL("LINE_POINT_OUT_OF_BOUNDS")
  return point
}

HistoryScrollRailPoint(target) {
  bounds := ReadCurrentLineBounds(target)
  ; The outer 3px edge is intentionally used instead of a guessed chat-content
  ; point. Remaining risk: LINE layout changes can move the scroll rail.
  point := { x: bounds.x + bounds.w - 3, y: bounds.y + Floor(bounds.h / 2), bounds: bounds }
  if (point.x <= bounds.x || point.x >= bounds.x + bounds.w - 1 || point.y <= bounds.y || point.y >= bounds.y + bounds.h - 1)
    LINE_GUARD_FAIL("LINE_POINT_OUT_OF_BOUNDS")
  return point
}

GuardedLineClick(target, point) {
  AssertExactLineFocus(target)
  currentBounds := ReadCurrentLineBounds(target)
  if !SameBounds(point.bounds, currentBounds)
    LINE_GUARD_FAIL("LINE_RECT_DRIFT")
  if (point.x < currentBounds.x || point.x >= currentBounds.x + currentBounds.w || point.y < currentBounds.y || point.y >= currentBounds.y + currentBounds.h)
    LINE_GUARD_FAIL("LINE_POINT_OUT_OF_BOUNDS")

  pointBuffer := Buffer(8, 0)
  NumPut("Int", point.x, pointBuffer, 0)
  NumPut("Int", point.y, pointBuffer, 4)
  hit := DllCall("WindowFromPoint", "Int64", NumGet(pointBuffer, 0, "Int64"), "Ptr")
  if (!hit || DllCall("GetAncestor", "Ptr", hit, "UInt", 2, "Ptr") != target.hwnd)
    LINE_GUARD_FAIL("LINE_CLICK_TARGET_UNVERIFIED")
  Click point.x, point.y
}

GuardedHistoryRailClick(target) {
  GuardedLineClick(target, HistoryScrollRailPoint(target))
}

GuardedLineSend(target, keys) {
  AssertExactLineFocus(target)
  Send keys
}
`;

const LINE_SEND_AHK_GUARDS = String.raw`
; CODEX_LINE_SEND_GUARDS_V1
; HISTORY_AHK_GUARDS supplies the exact LINE HWND/PID/focus and hit-test gates.
; These compositor points prove bounds and LINE HWND ownership only. Qt does not
; expose semantic composer or attachment-icon identity to this AHK path.

ComposerPoint(target) {
  bounds := ReadCurrentLineBounds(target)
  return RelativeLinePoint(target, Floor(bounds.w * 3 / 4), bounds.h - 100)
}

AttachmentPoint(target) {
  bounds := ReadCurrentLineBounds(target)
  return RelativeLinePoint(target, bounds.w - 360, bounds.h - 24)
}

IsExactLineOpenDialogTitle(title) {
  return title = "開啟" || title = "Open"
}

AssertExactLineFileDialogFocus(target, dialog) {
  active := WinExist("A")
  if (!active || active != dialog.hwnd)
    LINE_GUARD_FAIL("LINE_FILE_DIALOG_FOCUS_CHANGED")
  if (WinGetPID("ahk_id " active) != target.pid)
    LINE_GUARD_FAIL("LINE_FILE_DIALOG_PID_CHANGED")
  if (WinGetClass("ahk_id " active) != "#32770")
    LINE_GUARD_FAIL("LINE_FILE_DIALOG_CLASS_CHANGED")
  if !IsExactLineOpenDialogTitle(WinGetTitle("ahk_id " active))
    LINE_GUARD_FAIL("LINE_FILE_DIALOG_TITLE_CHANGED")
  if !DllCall("IsWindowEnabled", "Ptr", active, "Int")
    LINE_GUARD_FAIL("LINE_FILE_DIALOG_DISABLED")
}

AcquireExactLineOpenDialog(target) {
  deadline := A_TickCount + 3000
  Loop {
    dialogs := []
    for hwnd in WinGetList("ahk_class #32770") {
      try {
        if !DllCall("IsWindowVisible", "Ptr", hwnd, "Int")
          continue
        if !DllCall("IsWindowEnabled", "Ptr", hwnd, "Int")
          continue
        if (WinGetPID("ahk_id " hwnd) != target.pid)
          continue
        if !IsExactLineOpenDialogTitle(WinGetTitle("ahk_id " hwnd))
          continue
        dialogs.Push({ hwnd: hwnd, pid: target.pid })
      } catch {
        LINE_GUARD_FAIL("LINE_FILE_DIALOG_QUERY_FAILED")
      }
    }
    if (dialogs.Length > 1)
      LINE_GUARD_FAIL("LINE_FILE_DIALOG_NOT_UNIQUE")
    if (dialogs.Length = 1) {
      dialog := dialogs[1]
      WinActivate "ahk_id " dialog.hwnd
      if !WinWaitActive("ahk_id " dialog.hwnd,, 2)
        LINE_GUARD_FAIL("LINE_FILE_DIALOG_FOCUS_UNAVAILABLE")
      AssertExactLineFileDialogFocus(target, dialog)
      return dialog
    }
    if (A_TickCount >= deadline)
      LINE_GUARD_FAIL("LINE_FILE_DIALOG_UNAVAILABLE")
    Sleep 50
  }
}

SetExactLineFileName(target, dialog, filePath) {
  AssertExactLineFileDialogFocus(target, dialog)
  try {
    ControlFocus "Edit1", "ahk_id " dialog.hwnd
    AssertExactLineFileDialogFocus(target, dialog)
    ControlSetText filePath, "Edit1", "ahk_id " dialog.hwnd
    actualPath := ControlGetText("Edit1", "ahk_id " dialog.hwnd)
  } catch {
    LINE_GUARD_FAIL("LINE_FILE_DIALOG_FILENAME_UNAVAILABLE")
  }
  if (actualPath != filePath)
    LINE_GUARD_FAIL("LINE_FILE_DIALOG_FILENAME_MISMATCH")
  AssertExactLineFileDialogFocus(target, dialog)
}
`;

export class WindowsLineAutomation {
  constructor() {
    this.lineAppName = 'LINE';
    this.lineWinTitle = 'LINE';
    this.delayShort = 200; // for key stroke, mouse click simulation human-like
    this.delayMid = 600; // for short data loading
    this.delayMidLong = 1200; // for mid data loading
    this.delayLong = 3000; // for long data loading
    this.ahkPath = configuredAutoHotkeyPath(process.env.LINE_MCP_AUTOHOTKEY);
    this.executeFile = execFileAsync;
  }

  /**
   * Executes an AutoHotkey v2 script.
   * @param {string} script The AHK script content.
   * @returns {Promise<string>} The stdout from the script execution.
   */
  async runAhk(script) {
    const executable = configuredAutoHotkeyPath(this.ahkPath);
    if (!executable || !(await fs.stat(executable).catch(() => null))?.isFile()) {
      throw new Error('Configure LINE_MCP_AUTOHOTKEY with an absolute AutoHotkey v2 executable, or install v2 in its standard Program Files location.');
    }
    const scriptDirectory = await fs.mkdtemp(path.join(os.tmpdir(), 'line-mcp-ahk-'));
    const scriptPath = path.join(scriptDirectory, 'operation.ahk');
    // Prepend necessary AHK settings
    const fullScript = `#SingleInstance force
#Requires AutoHotkey v2.0
SendMode "Input"
SetWorkingDir A_ScriptDir
CoordMode "Pixel", "Screen"
SetTitleMatchMode 2
; CODEX_LINE_WINDOW_READY_GATE
; LINE.exe can be present before its exact top-level window is created.
SetTitleMatchMode 3
if !WinWait("LINE",, 10) {
  FileAppend "ERROR: LINE window was not ready within 10 seconds", "*"
  ExitApp(1)
}
SetTitleMatchMode 2
FileEncoding "UTF-8-RAW"
${script}
`;
    try {
      await fs.writeFile(scriptPath, fullScript, { flag: 'wx' });
      // Use buffer encoding to handle raw bytes
      const { stdout, stderr } = await this.executeFile(executable, [scriptPath], {
        encoding: 'buffer', shell: false, windowsHide: true
      });
      
      if (stderr && stderr.length > 0) {
        console.error('AHK helper reported diagnostics.');
      }
      if (!stdout || stdout.length === 0) {
        return '';
      }

      return decodeAhkOutput(stdout, 'stdout').trim();    } catch (error) {
      const ahkReadyErrorText = error.stdout == null
        ? ''
        : decodeAhkOutput(error.stdout, 'stdout').trim();      if (ahkReadyErrorText.startsWith('ERROR:')) {
        console.error('AHK Script Error: ' + ahkReadyErrorText);
        const ahkGuardCode = ahkReadyErrorText.match(/^ERROR:\s*(LINE_[A-Z_]+)$/)?.[1];
        const ahkError = new Error(ahkReadyErrorText);
        if (ahkGuardCode) ahkError.code = ahkGuardCode;
        throw ahkError;
      }
      console.error('AHK helper execution failed.');
      throw new Error('AHK execution failed. Verify the configured AutoHotkey v2 executable.');
    } finally {
      await fs.unlink(scriptPath).catch(error => { if (error.code !== 'ENOENT') throw error; });
      await fs.rmdir(scriptDirectory); // Remove only this now-empty owned directory.
    }
  }

  escapeAhkString(value) {
    return String(value)
      .replace(/\`/g, '\`\`')
      .replace(/"/g, '\`"')
      .replace(/\r?\n/g, ' ');
  }

  async isLineRunning() {
    try {
      const systemRoot = process.env.SystemRoot ?? process.env.SYSTEMROOT;
      if (typeof systemRoot !== 'string' || !path.isAbsolute(systemRoot)) return false;
      const result = execFileSync(path.join(systemRoot, 'System32', 'tasklist.exe'),
        ['/FI', 'IMAGENAME eq LINE.exe'], { encoding: 'utf8', shell: false, windowsHide: true });
      return result.toLowerCase().includes('line.exe');
    } catch (error) {
      // tasklist throws an error if no process is found
      return false;
    }
  }

  async activateLine(verifiedTarget) {
    if (verifiedTarget !== undefined && !validVerifiedLineTarget(verifiedTarget)) {
      return { success: false, code: 'LINE_TARGET_INVALID', error: 'A verified LINE window is required.' };
    }
    const targetExpression = verifiedTarget
      ? `AcquireExactLineTarget(${verifiedTarget.window_id}, "${this.escapeAhkString(verifiedTarget.title)}", ${verifiedTarget.pid})`
      : 'AcquireExactLineTarget()';
    const script = `
      ${HISTORY_AHK_GUARDS}
      ${targetExpression}
      ExitApp(0)
    `;
    try {
      await this.runAhk(script);
      return { success: true };
    } catch (e) {
      return { success: false, error: e.message };
    }
  }

  async selectChat(chatName) {
    const safeChatName = this.escapeAhkString(chatName);
    const script = `
      ${HISTORY_AHK_GUARDS}
      target := AcquireExactLineTarget()
      GuardedLineClick(target, RelativeLinePoint(target, 30, 110))
      Sleep ${this.delayMid}
      GuardedLineSend(target, "^+f")
      Sleep ${this.delayShort}
      A_Clipboard := "${safeChatName}"
      GuardedLineSend(target, "^a")
      GuardedLineSend(target, "{Delete}")
      Sleep ${this.delayShort}
      GuardedLineSend(target, "^v")
      Sleep ${this.delayMid}
      GuardedLineSend(target, "{Enter}")
      Sleep ${this.delayShort}
      GuardedLineClick(target, RelativeLinePoint(target, 200, 140))
      Sleep ${this.delayMid}
      Return
    `;
    try {
      await this.runAhk(script);
      return true;
    } catch (e) {
      if (typeof e?.code === 'string' && e.code.startsWith('LINE_')) throw e;
      console.error('selectChat failed', e);
      return false;
    }
  }

  async copyAllChatToClipboard() {
    const script = `
      ${HISTORY_AHK_GUARDS}
      ClipboardSequenceNumber() {
        return DllCall("User32.dll\\GetClipboardSequenceNumber", "UInt")
      }

      ClipboardOwnerProcessId() {
        ownerHwnd := DllCall("User32.dll\\GetClipboardOwner", "Ptr")
        if !ownerHwnd
          return 0
        ownerPid := 0
        ownerThread := DllCall("User32.dll\\GetWindowThreadProcessId", "Ptr", ownerHwnd, "UInt*", &ownerPid, "UInt")
        if (!ownerThread || !ownerPid)
          return 0
        return ownerPid
      }

      RestoreOwnedClipboard() {
        global savedClipboard, clipboardMutated, ownedClipboardSequence
        if !clipboardMutated
          return 1
        currentSequence := ClipboardSequenceNumber()
        if (ownedClipboardSequence = 0)
          return -1
        ; A changed sequence may belong to another clipboard writer. Preserve
        ; it instead of replacing it with our older saved formats. Win32 has
        ; no atomic compare-and-restore primitive, so a writer can still race
        ; after this comparison.
        if (currentSequence != ownedClipboardSequence) {
          clipboardMutated := false
          savedClipboard := ""
          return 0
        }
        try {
          A_Clipboard := savedClipboard
        } catch {
          return -1
        }
        clipboardMutated := false
        savedClipboard := ""
        return 1
      }

      ; OnExit callbacks must return zero or empty, otherwise they can cancel
      ; an ordinary ExitApp. This wrapper deliberately ignores the helper's
      ; status while still covering guard and runtime exits after mutation.
      RestoreClipboardOnExit(*) {
        RestoreOwnedClipboard()
      }

      target := AcquireExactLineTarget()
      ; Preserve the original second history focus click, but only on the
      ; same guarded right-edge rail used by pageUp.
      GuardedHistoryRailClick(target)
      Sleep ${this.delayShort}
      GuardedLineSend(target, "^a")
      Sleep ${this.delayMid}
      ; Snapshot the available clipboard formats immediately before temporary
      ; use. The sequence checks preserve foreign updates observed before
      ; cleanup; they do not control clipboard history or observers.
      savedClipboard := ClipboardAll()
      clipboardMutated := false
      ownedClipboardSequence := 0
      OnExit RestoreClipboardOnExit
      clipboardMutated := true
      A_Clipboard := ""
      ownedClipboardSequence := ClipboardSequenceNumber()
      if (ownedClipboardSequence = 0)
        LINE_GUARD_FAIL("LINE_CLIPBOARD_SEQUENCE_UNAVAILABLE")
      GuardedLineSend(target, "^c")
      ClipWait 2
      copySequence := ClipboardSequenceNumber()
      copyOwnerPid := ClipboardOwnerProcessId()
      if (copySequence = 0)
        LINE_GUARD_FAIL("LINE_CLIPBOARD_SEQUENCE_UNAVAILABLE")
      ; Ctrl+C must produce a new clipboard version owned by the exact LINE
      ; process. Otherwise another writer (or our sentinel clear) won the race.
      if (copySequence = ownedClipboardSequence || copyOwnerPid != target.pid)
        LINE_GUARD_FAIL("LINE_CLIPBOARD_SOURCE_UNVERIFIED")
      chatHistory := A_Clipboard
      ; Bracket the read so a foreign write during adoption is detected too.
      if (ClipboardSequenceNumber() != copySequence || ClipboardOwnerProcessId() != target.pid)
        LINE_GUARD_FAIL("LINE_CLIPBOARD_SOURCE_CHANGED")
      ownedClipboardSequence := copySequence
      restoreStatus := RestoreOwnedClipboard()
      if (restoreStatus = 0)
        LINE_GUARD_FAIL("LINE_CLIPBOARD_SOURCE_CHANGED")
      if (restoreStatus < 0)
        LINE_GUARD_FAIL("LINE_CLIPBOARD_RESTORE_FAILED")
      if (chatHistory != "") {
        FileAppend chatHistory, "*"
      } else {
        FileAppend "ERROR: Clipboard is empty", "*"
      }
    `;
    try {
      const result = await this.runAhk(script);
      if (!result || result.startsWith('ERROR:')) {
        console.error('copyAllChatToClipboard result', result);
        return result;
      }
      return result;
    } catch (e) {
      if (typeof e?.code === 'string' && e.code.startsWith('LINE_')) throw e;
      console.error('copyAllChatToClipboard failed', e);
      return null;
    }
  }

  async pageUp(times = 2) {
    const script = `
      ${HISTORY_AHK_GUARDS}
      target := AcquireExactLineTarget()
      ; This is the single history focus click. It stays on the outer rail,
      ; never at a guessed message/image bubble coordinate.
      GuardedHistoryRailClick(target)
      Sleep ${this.delayShort}
      GuardedLineSend(target, "{End}")
      Sleep ${this.delayShort}
      Loop ${times} {
        GuardedLineSend(target, "{PgUp}")
        Sleep ${this.delayShort}
      }
    `;
    await this.runAhk(script);
  }

  async switchToEnglish() {
    // On Windows, switching input method is complex.
    // A common method is to cycle with Alt+Shift.
    // For now, we assume the user has the correct (e.g., English) input method active.
    // This can be improved later with more advanced techniques if needed.
    const script = `
      WinActivate "${this.lineWinTitle}"
      Sleep ${this.delayShort}
      Send "!+^" ; Alt+Shift to cycle language (example, might not work for all)
    `;
    // We will just log a warning and proceed, as this is not reliable.
    console.warn("Switching to English on Windows is not reliably implemented. Assuming correct input method is active.");
    // await this.runAhk(script);
    return;
  }

  async stageFileManual(filePath, verifiedTarget) {
    const safeFilePath = this.escapeAhkString(filePath);
    if (verifiedTarget && !validVerifiedLineTarget(verifiedTarget)) {
      return { success: false, code: 'LINE_TARGET_INVALID', error: 'A verified LINE window is required.' };
    }
    const targetExpression = verifiedTarget
      ? `AcquireExactLineTarget(${verifiedTarget.window_id}, "${this.escapeAhkString(verifiedTarget.title)}", ${verifiedTarget.pid})`
      : 'AcquireExactLineTarget()';
    const script = `
      ${HISTORY_AHK_GUARDS}
      ${LINE_SEND_AHK_GUARDS}
      target := ${targetExpression}
      ${verifiedTarget ? `if (target.pid != ${verifiedTarget.pid})\n        LINE_GUARD_FAIL("LINE_TARGET_PID_CHANGED")` : ''}
      ; Ctrl+O works in the verified main and independent chat windows.
      ; Bind HWND/PID/title; never route the attachment to another LINE window.
      GuardedLineSend(target, "^o")
      Sleep ${this.delayMidLong}
      dialog := AcquireExactLineOpenDialog(target)
      SetExactLineFileName(target, dialog, "${safeFilePath}")
      Sleep ${this.delayShort}
      ; Do not click Open or press Enter: this only stages the selected path.
      FileAppend "READY", "*"
    `;
    try {
      const result = await this.runAhk(script);
      if (result !== 'READY') {
        return { success: false, error: 'LINE_FILE_STAGE_UNVERIFIED: filename readback did not complete.' };
      }
      return { success: true, error: null };
    } catch (e) {
      return { success: false, error: e.message, ...(e?.code ? { code: e.code } : {}) };
    }
  }

  async sendMessage(chatName, message, autoSend = false) {
    const failure = (result, fallback) => ({
      success: false,
      error: result?.error || fallback,
      ...(result?.code ? { code: result.code } : {}),
    });

    let result = await this._sendSingleMessageInit(chatName);
    if (result?.success !== true) return failure(result, 'LINE_SEND_INIT_FAILED');

    const lines = message.split(/\r\n|\n|\r/);
    for (let index = 0; index < lines.length; index++) {
      const line = lines[index];
      if (line) {
        result = await this._sendSingleMessage(chatName, line);
        if (result?.success !== true) return failure(result, 'LINE_SEND_TEXT_FAILED');
      }

      if (index < lines.length - 1) {
        result = await this._sendShiftEnter();
        if (result?.success !== true) return failure(result, 'LINE_SEND_NEWLINE_FAILED');
      }
    }

    if (autoSend) {
      result = await this._sendSingleMessageEnter();
      if (result?.success !== true) return failure(result, 'LINE_SEND_ENTER_FAILED');
    }

    return { success: true, error: null };
  }

  async _sendShiftEnter() {
    const script = `
      ${HISTORY_AHK_GUARDS}
      ${LINE_SEND_AHK_GUARDS}
      target := AcquireExactLineTarget()
      GuardedLineSend(target, "+{Enter}")
    `;
    try {
      await this.runAhk(script);
      return { success: true };
    } catch (e) {
      return { success: false, error: e.message, ...(e?.code ? { code: e.code } : {}) };
    }
  }

  async _sendSingleMessageInit(chatName) {
    const script = `
      ${HISTORY_AHK_GUARDS}
      ${LINE_SEND_AHK_GUARDS}
      target := AcquireExactLineTarget()
      ; This compositor location is guarded geometrically, not semantically.
      GuardedLineClick(target, ComposerPoint(target))
      Sleep ${this.delayShort}
      GuardedLineSend(target, "^a")
      GuardedLineSend(target, "{Delete}")
      Sleep ${this.delayLong}
    `;
    try {
      await this.runAhk(script);
      return { success: true };
    } catch (e) {
      return { success: false, error: e.message, ...(e?.code ? { code: e.code } : {}) };
    }
  }

  async _sendSingleMessage(chatName, message) {
    const safeMessage = this.escapeAhkString(message);
    const script = `
      ${HISTORY_AHK_GUARDS}
      ${LINE_SEND_AHK_GUARDS}
      target := AcquireExactLineTarget()
      A_Clipboard := "${safeMessage}"
      GuardedLineSend(target, "^v")
      Sleep ${this.delayShort}
    `;
    try {
      await this.runAhk(script);
      return { success: true };
    } catch (e) {
      return { success: false, error: e.message, ...(e?.code ? { code: e.code } : {}) };
    }
  }

  async _sendSingleMessageEnter() {
    const script = `
      ${HISTORY_AHK_GUARDS}
      ${LINE_SEND_AHK_GUARDS}
      target := AcquireExactLineTarget()
      GuardedLineSend(target, "{Enter}")
    `;
    try {
      await this.runAhk(script);
      return { success: true };
    } catch (e) {
      return { success: false, error: e.message, ...(e?.code ? { code: e.code } : {}) };
    }
  }

  async _sendSingleMessageBackspace() {
    const script = `
      ${HISTORY_AHK_GUARDS}
      ${LINE_SEND_AHK_GUARDS}
      target := AcquireExactLineTarget()
      GuardedLineSend(target, "{Backspace}")
      Sleep ${this.delayMidLong}
    `;
    try {
      await this.runAhk(script);
      return { success: true };
    } catch (e) {
      return { success: false, error: e.message, ...(e?.code ? { code: e.code } : {}) };
    }
  }

  async _sendSingleMessageClickMention() {
    const script = `
      SetTitleMatchMode 3
      Sleep ${this.delayLong}
      ; Get window position and size
      WinGetPos &winX, &winY, &winW, &winH, "${this.lineWinTitle}"
      ; Click at position (w-20, h/2) within the window
      CoordMode "Mouse", "Screen"
      scale := A_ScreenDPI / 96
      clickX := winX + winW * (3/4)
      clickY := winY + winH - 130 * scale
      Click clickX, clickY
      Sleep ${this.delayShort}

      ; Get window position and size
      WinGetPos &winX, &winY, &winW, &winH, "${this.lineWinTitle}"
      ; Click at position (w-20, h/2) within the window
      CoordMode "Mouse", "Screen"
      clickX := winX + winW - 20 * scale
      clickY := winY + winH - 50 * scale
      Click clickX, clickY
    `;
    try {
      await this.runAhk(script);
      return { success: true };
    } catch (e) {
      return { success: false, error: e.message };
    }
  }
}
