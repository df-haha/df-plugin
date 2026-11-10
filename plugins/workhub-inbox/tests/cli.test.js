"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");

const { main, parseArgs } = require("../scripts/inbox-sync.js");

function makeHome(rows) {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "da-home-"));
  fs.writeFileSync(path.join(home, "CURRENT"), "1");
  const gen = path.join(home, "gen", "1");
  fs.mkdirSync(gen, { recursive: true });
  fs.writeFileSync(path.join(gen, "state.json"), JSON.stringify({ known_chats: [] }));
  fs.writeFileSync(path.join(gen, "ledger.jsonl"), rows.map((r) => JSON.stringify(r)).join("\n"));
  return home;
}

function row(id, overrides = {}) {
  return { id, title: `待辦 ${id}`, priority: "P1", due: null, status: "open", src_ids: ["mail:x"], created_at: "2026-11-09T08:00:00+08:00", ...overrides };
}

function fakeClient(existing = []) {
  const calls = [];
  let nextId = 100;
  return {
    calls,
    async callTool(name, args) {
      calls.push({ name, args });
      if (name === "pm_list_personal_tasks") return { items: args.view === "completed" ? [] : existing };
      if (name === "pm_create_personal_task") return { id: nextId++, deduplicated: false };
      if (name === "pm_complete_personal_task") return { id: args.task_id };
      throw new Error(`unexpected ${name}`);
    },
  };
}

function run(argv, client, home) {
  const out = [];
  const code = main(argv, {
    client,
    daHome: home,
    now: new Date("2026-11-10T03:00:00Z"),
    print: (s) => out.push(s),
  });
  return Promise.resolve(code).then((c) => ({ code: c, out: out.join("\n") }));
}

const writes = (client) => client.calls.filter((c) => c.name !== "pm_list_personal_tasks");

test("parseArgs：預設 dry-run、上限 20、P0–P2", () => {
  const opts = parseArgs(["sync", "--source", "daily-agenda"]);
  assert.equal(opts.apply, false);
  assert.equal(opts.maxWrites, 20);
  assert.deepEqual(opts.priorities, ["P0", "P1", "P2"]);
});

test("parseArgs：不認得的參數或來源報錯", () => {
  assert.throws(() => parseArgs(["sync", "--source", "gmail"]), /來源/);
  assert.throws(() => parseArgs(["sync", "--bogus"]), /參數/);
  assert.throws(() => parseArgs(["sync", "--priorities", "P9"]), /優先級/);
});

test("dry-run：只讀不寫，輸出預計建立筆數", async () => {
  const client = fakeClient();
  const { code, out } = await run(["sync", "--source", "daily-agenda", "--json"], client, makeHome([row("T-1"), row("T-2")]));
  assert.equal(code, 0);
  assert.equal(writes(client).length, 0);
  const report = JSON.parse(out);
  assert.equal(report.mode, "dry-run");
  assert.equal(report.counts.create, 2);
});

test("--json 輸出不含待辦標題", async () => {
  const client = fakeClient();
  const { out } = await run(["sync", "--source", "daily-agenda", "--json"], client, makeHome([row("T-1", { title: "機密標題字串" })]));
  assert.ok(!out.includes("機密標題字串"));
});

test("--apply：遵守 --max-writes，剩下的列為未執行", async () => {
  const client = fakeClient();
  const home = makeHome([row("T-1"), row("T-2"), row("T-3")]);
  const { code, out } = await run(["sync", "--source", "daily-agenda", "--apply", "--max-writes", "2", "--json"], client, home);
  assert.equal(code, 0);
  assert.equal(writes(client).length, 2);
  const report = JSON.parse(out);
  assert.equal(report.results.created, 2);
  assert.equal(report.results.deferred, 1);
});

test("--apply：先完成再建立，完成的 task_id 對得上", async () => {
  const existing = [{ id: 7, source: "outlook", external_id: "agenda:T-9:2026-11-09T08:00:00+08:00", workflow_state: "active", completed_at: null }];
  const client = fakeClient(existing);
  const home = makeHome([row("T-1"), row("T-9", { status: "done" })]);
  await run(["sync", "--source", "daily-agenda", "--apply", "--json"], client, home);
  const w = writes(client);
  assert.equal(w[0].name, "pm_complete_personal_task");
  assert.equal(w[0].args.task_id, 7);
  assert.equal(w[1].name, "pm_create_personal_task");
});

test("--apply：單筆寫入失敗不中斷其餘，結束碼 3", async () => {
  const client = fakeClient();
  const original = client.callTool;
  client.callTool = async (name, args) => {
    if (name === "pm_create_personal_task" && args.external_id.includes("T-1")) throw new Error("boom");
    return original(name, args);
  };
  const { code, out } = await run(["sync", "--source", "daily-agenda", "--apply", "--json"], client, makeHome([row("T-1"), row("T-2")]));
  assert.equal(code, 3);
  const report = JSON.parse(out);
  assert.equal(report.results.created, 1);
  assert.equal(report.results.failed, 1);
});

test("--apply：去重命中（已取消或已完成的舊待辦）不佔寫入上限", async () => {
  const client = fakeClient();
  const original = client.callTool;
  client.callTool = async (name, args) => {
    if (name === "pm_create_personal_task" && args.external_id.includes("T-1")) return { id: 1, deduplicated: true };
    return original(name, args);
  };
  const home = makeHome([row("T-1", { priority: "P0" }), row("T-2"), row("T-3")]);
  const { out } = await run(["sync", "--source", "daily-agenda", "--apply", "--max-writes", "1", "--json"], client, home);
  const report = JSON.parse(out);
  assert.equal(report.results.deduplicated, 1);
  assert.equal(report.results.created, 1);
  assert.equal(report.results.deferred, 1);
});

test("--only／--exclude：用代碼挑選或排除要建立的項目", async () => {
  const home = makeHome([row("T-1001"), row("T-1002"), row("T-1003")]);
  const only = JSON.parse((await run(["sync", "--source", "daily-agenda", "--only", "T-1001,T-1003", "--json"], fakeClient(), home)).out);
  assert.deepEqual(only.create.map((c) => c.agendaId).sort(), ["T-1001", "T-1003"]);
  const excl = JSON.parse((await run(["sync", "--source", "daily-agenda", "--exclude", "T-1002", "--json"], fakeClient(), home)).out);
  assert.deepEqual(excl.create.map((c) => c.agendaId).sort(), ["T-1001", "T-1003"]);
  assert.equal(excl.counts.excluded, 1);
});

test("parseArgs：--only 代碼格式不符報錯", () => {
  assert.throws(() => parseArgs(["sync", "--only", "T-1;rm"]), /代碼/);
});

test("文字輸出：標題去除控制字元與換行並加引號，沒帶 --show-titles 不顯示對話名稱", async () => {
  const state = { known_chats: [{ ref: "chat:ab12", name: "機密群組" }] };
  const home = makeHome([row("T-1", { title: "第一行\n\u001b[31m紅字\u0007", src_ids: ["line:chat:ab12:m:1"] })]);
  fs.writeFileSync(path.join(home, "gen", "1", "state.json"), JSON.stringify(state));
  const hidden = (await run(["sync", "--source", "daily-agenda"], fakeClient(), home)).out;
  assert.ok(!hidden.includes("機密群組"));
  assert.ok(!hidden.includes("紅字"));
  const shown = (await run(["sync", "--source", "daily-agenda", "--show-titles"], fakeClient(), home)).out;
  assert.ok(shown.includes("機密群組"));
  assert.ok(shown.includes("\"第一行 [31m紅字\""));
  assert.ok(!/[\u0000-\u001f\u007f]/.test(shown.replace(/\n/g, "")));
});

test("讀不到 agenda 資料：結束碼 2，不呼叫 Work Hub", async () => {
  const client = fakeClient();
  const { code } = await run(["sync", "--source", "daily-agenda"], client, path.join(os.tmpdir(), "no-such-da-home"));
  assert.equal(code, 2);
  assert.equal(client.calls.length, 0);
});
