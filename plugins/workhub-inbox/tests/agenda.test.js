"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");

const { loadAgenda, resolveDaHome, toInboxItems, taipeiDate } = require("../scripts/lib/agenda.js");

function row(overrides = {}) {
  return {
    id: "T-1234",
    title: "回覆報價",
    priority: "P1",
    due: null,
    status: "open",
    src_ids: ["line:chat:abc123:msg:1"],
    created_at: "2026-11-09T23:30:00+08:00",
    updated_at: "2026-11-09T23:30:00+08:00",
    maybe_done_since: null,
    maybe_done_reason: null,
    history: [],
    ...overrides,
  };
}

const STATE = { known_chats: [{ ref: "chat:abc123", name: "業務群組" }] };

test("toInboxItems：LINE 來源帶對話名稱、external_id 由代碼＋建立時間組成", () => {
  const [it] = toInboxItems([row()], STATE);
  assert.equal(it.source, "line");
  assert.equal(it.sourceLabel, "LINE");
  assert.equal(it.sourceName, "LINE・業務群組");
  assert.equal(it.externalId, "agenda:T-1234:2026-11-09T23:30:00+08:00");
  assert.equal(it.priority, 2);
  assert.equal(it.createdDate, "2026-11-09");
});

test("toInboxItems：信件與行事曆都歸 outlook，標籤不同", () => {
  const items = toInboxItems([
    row({ id: "T-1", src_ids: ["mail:AAMk"] }),
    row({ id: "T-2", src_ids: ["event:XYZ"] }),
  ], STATE);
  assert.deepEqual(items.map((i) => [i.source, i.sourceLabel]), [["outlook", "信件"], ["outlook", "行事曆"]]);
});

test("toInboxItems：沒有來源或不認得的優先級的列略過", () => {
  const items = toInboxItems([row({ src_ids: [] }), row({ id: "T-9", priority: "P7" })], STATE);
  assert.equal(items.length, 0);
});

test("toInboxItems：期限只取日期部分", () => {
  const [it] = toInboxItems([row({ due: "2026-11-12T18:00:00+08:00" })], STATE);
  assert.equal(it.due, "2026-11-12");
});

test("taipeiDate：UTC 跨日要換成台北日期", () => {
  assert.equal(taipeiDate("2026-11-09T16:30:00Z"), "2026-11-10");
  assert.equal(taipeiDate(new Date("2026-11-09T15:59:00Z")), "2026-11-09");
});

test("loadAgenda：讀 CURRENT generation 的 ledger 與 state", () => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "da-home-"));
  fs.writeFileSync(path.join(home, "CURRENT"), "19\n");
  const gen = path.join(home, "gen", "19");
  fs.mkdirSync(gen, { recursive: true });
  fs.writeFileSync(path.join(gen, "state.json"), JSON.stringify(STATE));
  fs.writeFileSync(path.join(gen, "ledger.jsonl"), `${JSON.stringify(row())}\n\n${JSON.stringify(row({ id: "T-2" }))}\n`);
  const { ledger, state } = loadAgenda(home);
  assert.equal(ledger.length, 2);
  assert.equal(state.known_chats.length, 1);
});

test("loadAgenda：沒有 CURRENT 時丟出可辨識的錯誤", () => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "da-home-"));
  assert.throws(() => loadAgenda(home), { name: "AgendaSourceError" });
});

test("resolveDaHome：原生 Windows 用 %USERPROFILE%\\.daily-agenda（與 daily-agenda 相同位置）", () => {
  const home = resolveDaHome({ env: { USERPROFILE: "C:\\Users\\amy" }, platform: "win32" });
  assert.equal(home, "C:\\Users\\amy\\.daily-agenda");
});

test("resolveDaHome：認得 daily-agenda 自己的 DAILY_AGENDA_HOME", () => {
  assert.equal(resolveDaHome({ env: { DAILY_AGENDA_HOME: "D:\\agenda" }, platform: "win32" }), "D:\\agenda");
  assert.equal(resolveDaHome({ env: { DAILY_AGENDA_HOME: "/data/agenda" }, platform: "linux" }), "/data/agenda");
});

test("resolveDaHome：--da-home 優先於環境變數", () => {
  assert.equal(resolveDaHome({ explicit: "/x", env: { DAILY_AGENDA_HOME: "/y", DA_HOME: "/z" }, platform: "win32" }), "/x");
});
