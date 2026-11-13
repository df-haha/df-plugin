"use strict";

// daily-agenda 來源轉接器：唯讀 CURRENT generation 的 ledger.jsonl＋state.json，
// 轉成與來源無關的收件匣項目。不讀 runs/ 的原始訊息；標題屬不可信內容，只當資料傳遞。

const { execFileSync } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const TIME_ZONE = "Asia/Taipei";
const PRIORITY_MAP = { P0: 1, P1: 2, P2: 3 };
const SOURCE_BY_PREFIX = [
  { prefix: "line:", source: "line", label: "LINE" },
  { prefix: "mail:", source: "outlook", label: "信件" },
  { prefix: "event:", source: "outlook", label: "行事曆" },
];
const LINE_CHAT = /^line:(chat:[0-9a-f]+):/;
const REPORT_CODE = /^R-[A-Z0-9]{3}$/;
const AGENDA_ID = /^T-\d{4}$/;

class AgendaSourceError extends Error {
  constructor(message) {
    super(message);
    this.name = "AgendaSourceError";
  }
}

/** ISO 字串或 Date → 台北日期 YYYY-MM-DD。 */
function taipeiDate(value) {
  const date = value instanceof Date ? value : new Date(value);
  return new Intl.DateTimeFormat("en-CA", { timeZone: TIME_ZONE, year: "numeric", month: "2-digit", day: "2-digit" }).format(date);
}

/**
 * 找 daily-agenda home：明送 > DAILY_AGENDA_HOME > Windows %USERPROFILE% > WSL 換算 > 家目錄。
 * 只認 daily-agenda 自己認的 DAILY_AGENDA_HOME，兩邊才會讀寫同一個資料夾。
 */
function resolveDaHome({ explicit, env = process.env, platform = process.platform, exec = execFileSync } = {}) {
  if (explicit) return explicit;
  if (env.DAILY_AGENDA_HOME) return env.DAILY_AGENDA_HOME;
  if (platform === "win32") return path.win32.join(env.USERPROFILE || os.homedir(), ".daily-agenda");
  if (platform === "linux" && /microsoft/i.test(os.release())) {
    try {
      const winProfile = exec("cmd.exe", ["/c", "echo %USERPROFILE%"], { cwd: "/", encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] })
        .split(/\r?\n/).map((s) => s.trim()).filter(Boolean).pop();
      const wslPath = exec("wslpath", ["-u", winProfile], { encoding: "utf8" }).trim();
      return path.join(wslPath, ".daily-agenda");
    } catch {
      throw new AgendaSourceError("無法換算 Windows 使用者目錄；請用 --da-home 或 DAILY_AGENDA_HOME 指定 daily-agenda 資料夾");
    }
  }
  return path.join(os.homedir(), ".daily-agenda");
}

/** 讀 CURRENT generation，回傳 { ledger, state }。 */
function loadAgenda(home) {
  let genDir;
  try {
    const gen = fs.readFileSync(path.join(home, "CURRENT"), "utf8").trim();
    genDir = path.join(home, "gen", gen);
    const state = JSON.parse(fs.readFileSync(path.join(genDir, "state.json"), "utf8"));
    const ledger = fs.readFileSync(path.join(genDir, "ledger.jsonl"), "utf8")
      .split(/\r?\n/).filter((line) => line.trim()).map((line) => JSON.parse(line));
    return { ledger, state };
  } catch (err) {
    throw new AgendaSourceError(`讀不到 daily-agenda 資料（${err.code || err.name}）`);
  }
}

/**
 * 報表（彙整頁）→ 它列出的 agenda 代碼。對照存在 CURRENT generation 的 state.json：
 * report_code（R-加 3 碼）與 report_index（{"項次": "T-xxxx"}，含頁上的未完成、可能已完成、收合項目）。
 * daily-agenda 只保留最新一份報表的對照，所以只認最新一份（和它的 confirm 一樣，舊代碼視為過期）。
 * @param {object} state
 * @param {string} requested 已驗過格式的 R-XXX 或 "latest"
 * @returns {{code: string, ids: Set<string>}}
 */
function reportTodoIds(state, requested) {
  const current = typeof state.report_code === "string" && REPORT_CODE.test(state.report_code) ? state.report_code : null;
  if (!current) throw new AgendaSourceError("daily-agenda 還沒有產生過彙整頁（找不到任何報表代碼）；請先跑一次 daily-agenda");
  if (requested !== "latest" && requested !== current) {
    throw new AgendaSourceError(`找不到報表 ${requested}：daily-agenda 只保留最新一份報表，目前是 ${current}。請用最新彙整頁的代碼，或改用 --report latest`);
  }
  const index = state.report_index && typeof state.report_index === "object" ? Object.values(state.report_index) : [];
  return { code: current, ids: new Set(index.filter((id) => typeof id === "string" && AGENDA_ID.test(id))) };
}

function sourceOf(srcId) {
  return SOURCE_BY_PREFIX.find((s) => srcId.startsWith(s.prefix)) || null;
}

function displayName(srcId, source, chatNames) {
  if (source.source !== "line") return source.label;
  const match = LINE_CHAT.exec(srcId);
  const name = match ? chatNames.get(match[1]) : "";
  return name ? `LINE・${name}` : "LINE";
}

/** ledger 列 → 收件匣項目；沒有可辨識來源或優先級的列略過。 */
function toInboxItems(ledger, state) {
  const chatNames = new Map((state.known_chats || []).filter((c) => c.ref).map((c) => [c.ref, c.name || ""]));
  const items = [];
  for (const row of ledger) {
    const firstSrc = (row.src_ids || [])[0];
    const source = firstSrc ? sourceOf(firstSrc) : null;
    const priority = PRIORITY_MAP[row.priority];
    if (!source || !priority || !row.id || !row.created_at) continue;
    items.push({
      agendaId: row.id,
      externalId: `agenda:${row.id}:${row.created_at}`,
      source: source.source,
      sourceLabel: source.label,
      sourceName: displayName(firstSrc, source, chatNames),
      title: String(row.title || ""),
      agendaPriority: row.priority,
      priority,
      due: row.due ? String(row.due).slice(0, 10) : null,
      status: row.status,
      createdDate: taipeiDate(row.created_at),
    });
  }
  return items;
}

module.exports = { AgendaSourceError, REPORT_CODE, loadAgenda, reportTodoIds, resolveDaHome, taipeiDate, toInboxItems };
