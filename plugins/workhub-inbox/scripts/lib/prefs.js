"use strict";

// 本機偏好檔：使用者選的「報表模式只收幾天內的待辦」。放在狀態資料夾根層（不分端點與金鑰，偏好跟人走）。
// 寫入先寫 .tmp 再 rename，可重跑；讀到損壞的檔案不當掉，回報 error 讓呼叫端退回預設。

const fs = require("node:fs");
const path = require("node:path");

const PREFS_FILE = "prefs.json";
const VERSION = 1;
const MAX_FRESH_DAYS = 3650;
const FRESH_DAYS_TEXT = /^[1-9]\d{0,3}$/;

/**
 * 「天數」字串 → 正整數或 "all"；不合法回 null。只收十進位數字字面，不接受 1e2、空白、小數。
 * @param {unknown} value
 * @returns {number|"all"|null}
 */
function parseFreshDays(value) {
  if (value === "all") return "all";
  const text = typeof value === "number" ? String(value) : value;
  if (typeof text !== "string" || !FRESH_DAYS_TEXT.test(text)) return null;
  const days = Number(text);
  return days <= MAX_FRESH_DAYS ? days : null;
}

/** @returns {{saved: boolean, freshDays: number|"all"|null, error: boolean}} */
function loadPrefs(dir) {
  let raw;
  try {
    raw = fs.readFileSync(path.join(dir, PREFS_FILE), "utf8");
  } catch (err) {
    return { saved: false, freshDays: null, error: err.code !== "ENOENT" };
  }
  try {
    const data = JSON.parse(raw);
    const freshDays = data && data.version === VERSION ? parseFreshDays(data.freshDays) : null;
    return freshDays === null ? { saved: false, freshDays: null, error: true } : { saved: true, freshDays, error: false };
  } catch {
    return { saved: false, freshDays: null, error: true };
  }
}

function savePrefs(dir, freshDays) {
  fs.mkdirSync(dir, { recursive: true, mode: 0o700 });
  const file = path.join(dir, PREFS_FILE);
  const tmp = `${file}.${process.pid}.tmp`;
  try {
    fs.writeFileSync(tmp, `${JSON.stringify({ version: VERSION, freshDays }, null, 2)}\n`, { encoding: "utf8", mode: 0o600 });
    fs.renameSync(tmp, file);
  } catch (err) {
    fs.rmSync(tmp, { force: true });
    throw err;
  }
}

function clearPrefs(dir) {
  fs.rmSync(path.join(dir, PREFS_FILE), { force: true });
}

module.exports = { MAX_FRESH_DAYS, clearPrefs, loadPrefs, parseFreshDays, savePrefs };
