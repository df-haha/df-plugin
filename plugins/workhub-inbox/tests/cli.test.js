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
      // 與 Work Hub 實際回應同形：completePersonalTask 回 { task, next, completed_children, already_completed }
      if (name === "pm_complete_personal_task") return { task: { id: args.task_id }, next: null, completed_children: [], already_completed: false };
      throw new Error(`unexpected ${name}`);
    },
  };
}

function run(argv, client, home, stateDir = fs.mkdtempSync(path.join(os.tmpdir(), "wi-state-"))) {
  const out = [];
  const code = main(argv, {
    client,
    env: {},
    daHome: home,
    stateDir,
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

test("--apply：失敗的寫入也算進上限（結果不確定時可能已寫入）", async () => {
  const client = fakeClient();
  client.callTool = (orig => async (name, args) => {
    if (name === "pm_create_personal_task") { client.calls.push({ name, args }); throw new Error("socket hang up"); }
    return orig(name, args);
  })(client.callTool);
  const { code, out } = await run(["sync", "--source", "daily-agenda", "--apply", "--max-writes", "2", "--json"], client, makeHome([row("T-1"), row("T-2"), row("T-3")]));
  assert.equal(code, 3);
  assert.equal(writes(client).length, 2);
  assert.equal(JSON.parse(out).results.deferred, 1);
});

test("--apply：寫入時遇到 401 立刻停止，結束碼 1", async () => {
  const { WorkhubError } = require("../scripts/lib/workhub-client.js");
  const client = fakeClient();
  client.callTool = (orig => async (name, args) => {
    if (name === "pm_create_personal_task") { client.calls.push({ name, args }); throw new WorkhubError("HTTP 401", { status: 401 }); }
    return orig(name, args);
  })(client.callTool);
  const { code, out } = await run(["sync", "--source", "daily-agenda", "--apply", "--json"], client, makeHome([row("T-1"), row("T-2")]));
  assert.equal(code, 1);
  assert.equal(writes(client).length, 1);
  const r = JSON.parse(out).results;
  assert.equal(r.notAttempted, 1);
  assert.equal(r.deferred, 0);
});

test("401 中止的文字摘要不誤報「超過上限」", async () => {
  const { WorkhubError } = require("../scripts/lib/workhub-client.js");
  const client = fakeClient();
  client.callTool = (orig => async (name, args) => {
    if (name === "pm_create_personal_task") throw new WorkhubError("HTTP 401", { status: 401 });
    return orig(name, args);
  })(client.callTool);
  const { out } = await run(["sync", "--source", "daily-agenda", "--apply"], client, makeHome([row("T-1"), row("T-2"), row("T-3")]));
  assert.match(out, /中止後未執行 2/);
  assert.doesNotMatch(out, /7 天/);
});

test("對照檔：清單看得到的狀態覆寫舊紀錄", async () => {
  const stateDir = fs.mkdtempSync(path.join(os.tmpdir(), "wi-state-"));
  const key = "agenda:T-1:2026-11-09T08:00:00+08:00";
  fs.writeFileSync(path.join(stateDir, "agenda-map.json"), JSON.stringify({ version: 1, tasks: { [key]: { taskId: 5, completed: false } } }));
  const client = fakeClient([{ id: 5, source: "outlook", external_id: key, workflow_state: "completed", completed_at: "2026-11-10T00:00:00Z" }]);
  await run(["sync", "--source", "daily-agenda", "--apply", "--json"], client, makeHome([row("T-1")]), stateDir);
  const map = JSON.parse(fs.readFileSync(path.join(stateDir, "agenda-map.json"), "utf8"));
  assert.equal(map.tasks[key].completed, true);
});

test("對照檔：建立後記下 task id，清單查不到時仍能勾完成", async () => {
  const stateDir = fs.mkdtempSync(path.join(os.tmpdir(), "wi-state-"));
  await run(["sync", "--source", "daily-agenda", "--apply", "--json"], fakeClient(), makeHome([row("T-1")]), stateDir);
  const map = JSON.parse(fs.readFileSync(path.join(stateDir, "agenda-map.json"), "utf8"));
  const key = "agenda:T-1:2026-11-09T08:00:00+08:00";
  assert.equal(map.tasks[key].taskId, 100);
  // 隔天 agenda 標 done，但清單（超過 500 筆）查不到這筆
  const client = fakeClient([]);
  const { out } = await run(["sync", "--source", "daily-agenda", "--apply", "--json"], client, makeHome([row("T-1", { status: "done" })]), stateDir);
  const done = writes(client).filter((c) => c.name === "pm_complete_personal_task");
  assert.equal(done.length, 1);
  assert.equal(done[0].args.task_id, 100);
  assert.equal(JSON.parse(out).results.completed, 1);
  const after = JSON.parse(fs.readFileSync(path.join(stateDir, "agenda-map.json"), "utf8"));
  assert.equal(after.tasks[key].completed, true);
});

test("對照檔：已記為完成的不再送完成；清單顯示已取消的以清單為準", async () => {
  const stateDir = fs.mkdtempSync(path.join(os.tmpdir(), "wi-state-"));
  const keyA = "agenda:T-1:2026-11-09T08:00:00+08:00";
  const keyB = "agenda:T-2:2026-11-09T08:00:00+08:00";
  fs.writeFileSync(path.join(stateDir, "agenda-map.json"), JSON.stringify({ version: 1, tasks: { [keyA]: { taskId: 5, completed: true }, [keyB]: { taskId: 6, completed: false } } }));
  const client = fakeClient([{ id: 6, source: "outlook", external_id: keyB, workflow_state: "cancelled", completed_at: null }]);
  await run(["sync", "--source", "daily-agenda", "--apply", "--json"], client, makeHome([row("T-1", { status: "done" }), row("T-2", { status: "done" })]), stateDir);
  assert.equal(writes(client).length, 0);
});

test("stateDirFor：端點或金鑰不同就用不同子資料夾，路徑不含金鑰原文", () => {
  const { stateDirFor } = require("../scripts/inbox-sync.js");
  const a = stateDirFor("/base", "https://dev.example/mcp", "key-A");
  assert.notEqual(a, stateDirFor("/base", "https://prod.example/mcp", "key-A"));
  assert.notEqual(a, stateDirFor("/base", "https://dev.example/mcp", "key-B"));
  assert.equal(a, stateDirFor("/base", "https://dev.example/mcp", "key-A"));
  assert.ok(a.startsWith(path.join("/base", "")));
  assert.ok(!a.includes("key-A"));
});

test("dry-run 不寫對照檔", async () => {
  const stateDir = fs.mkdtempSync(path.join(os.tmpdir(), "wi-state-"));
  await run(["sync", "--source", "daily-agenda", "--json"], fakeClient(), makeHome([row("T-1")]), stateDir);
  assert.equal(fs.existsSync(path.join(stateDir, "agenda-map.json")), false);
});

test("只認 agenda: 開頭的既有待辦（其他來源同 id 不會被當成自己的）", async () => {
  const client = fakeClient([{ id: 9, source: "line", external_id: "T-1", workflow_state: "active", completed_at: null }]);
  const { out } = await run(["sync", "--source", "daily-agenda", "--json"], client, makeHome([row("T-1")]));
  assert.equal(JSON.parse(out).counts.create, 1);
});

test("摘要：有延後筆數時提醒可能過期", async () => {
  const { out } = await run(["sync", "--source", "daily-agenda", "--apply", "--max-writes", "1"], fakeClient(), makeHome([row("T-1"), row("T-2")]));
  assert.match(out, /超過上限.*7 天/);
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

test("parseArgs：不接受 --endpoint（端點只能來自環境變數）", () => {
  assert.throws(() => parseArgs(["sync", "--endpoint", "https://pm.example/mcp"]), /參數/);
});

test("說明文件不再教 --endpoint", () => {
  for (const file of ["../README.md", "../skills/workhub-inbox/SKILL.md"]) {
    const text = fs.readFileSync(path.join(__dirname, file), "utf8");
    assert.ok(!text.includes("--endpoint"), file);
  }
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
