"use strict";

// --fresh-days 與本機偏好（prefs）：報表模式下舊待辦要不要建立，由使用者自己選。
// 優先序：--fresh-days 旗標 ＞ 本機偏好（只在報表模式採用）＞ 預設 7 天。
// 一律用假金鑰、假 daily-agenda 資料夾與 127.0.0.1 假伺服器；子行程不繼承本機環境。

const assert = require("node:assert/strict");
const { execFile } = require("node:child_process");
const fs = require("node:fs");
const http = require("node:http");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");

const { parseArgs } = require("../scripts/inbox-sync.js");

const CLI = path.join(__dirname, "..", "scripts", "inbox-sync.js");
const KEY = "whk_FAKE_fresh_key_0123456789abcdef";
const DAY = 86_400_000;
const PREFS_FILE = "prefs.json";

const daysAgo = (n) => new Date(Date.now() - n * DAY).toISOString();

function row(id, ageDays, overrides = {}) {
  return { id, title: `機密標題 ${id}`, priority: "P1", due: null, status: "open", src_ids: ["mail:x"], created_at: daysAgo(ageDays), ...overrides };
}

/** 新 1 天、舊 10 天、更舊 40 天各一筆，全部列在報表 R-7KQ。 */
function makeHome(rows = [row("T-1001", 1), row("T-1002", 10), row("T-1003", 40)]) {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "da-home-"));
  fs.writeFileSync(path.join(home, "CURRENT"), "1\n");
  const gen = path.join(home, "gen", "1");
  fs.mkdirSync(gen, { recursive: true });
  const index = Object.fromEntries(rows.map((r, i) => [String(i + 1), r.id]));
  fs.writeFileSync(path.join(gen, "state.json"), JSON.stringify({ known_chats: [], report_code: "R-7KQ", report_index: index }));
  fs.writeFileSync(path.join(gen, "ledger.jsonl"), rows.map((r) => JSON.stringify(r)).join("\n"));
  return home;
}

async function fakeServer() {
  const calls = [];
  let nextId = 500;
  const server = http.createServer((req, res) => {
    let body = "";
    req.on("data", (c) => { body += c; });
    req.on("end", () => {
      const rpc = JSON.parse(body);
      const { name, arguments: args } = rpc.params;
      calls.push({ name, args });
      const out = name === "pm_list_personal_tasks" ? { items: [] } : { id: nextId++, deduplicated: false };
      res.setHeader("content-type", "application/json");
      res.end(JSON.stringify({ jsonrpc: "2.0", id: rpc.id, result: { structuredContent: out } }));
    });
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  return { server, calls, url: `http://127.0.0.1:${server.address().port}/mcp` };
}

function runCli(args, env) {
  return new Promise((resolve) => {
    execFile(process.execPath, [CLI, ...args], { env, encoding: "utf8", timeout: 60_000 }, (err, stdout, stderr) => {
      resolve({ code: err ? err.code : 0, stdout, stderr });
    });
  });
}

/** 每個測試一個獨立的狀態資料夾（偏好檔放這裡）。 */
async function setup(fn) {
  const srv = await fakeServer();
  const stateBase = fs.mkdtempSync(path.join(os.tmpdir(), "wi-base-"));
  const env = { PATH: process.env.PATH, SYSTEMROOT: process.env.SYSTEMROOT, WORKHUB_INBOX_API_KEY: KEY, WORKHUB_INBOX_ENDPOINT: srv.url, WORKHUB_INBOX_STATE_DIR: stateBase };
  const created = () => srv.calls.filter((c) => c.name === "pm_create_personal_task").map((c) => c.args.external_id.split(":")[1]).sort();
  try {
    return await fn({ stateBase, created, calls: srv.calls, cli: (args) => runCli(args, env) });
  } finally {
    srv.server.close();
  }
}

const sync = (home, ...extra) => ["sync", "--source", "daily-agenda", "--da-home", home, "--apply", "--json", "--report", "R-7KQ", ...extra];

test("parseArgs：--fresh-days 只收 1–3650 的正整數或 all", () => {
  assert.equal(parseArgs(["sync", "--fresh-days", "30"]).freshDays, 30);
  assert.equal(parseArgs(["sync", "--fresh-days", "1"]).freshDays, 1);
  assert.equal(parseArgs(["sync", "--fresh-days", "3650"]).freshDays, 3650);
  assert.equal(parseArgs(["sync", "--fresh-days", "all"]).freshDays, "all");
  assert.equal(parseArgs(["sync"]).freshDays, null);
  for (const bad of ["0", "-1", "3651", "1.5", "07x", "ALL", "", " 7", "1e2", "Infinity"]) {
    assert.throws(() => parseArgs(["sync", "--fresh-days", bad]), /--fresh-days/, JSON.stringify(bad));
  }
});

test("--fresh-days 格式錯誤：結束碼 1、不送任何請求", async () => {
  await setup(async ({ cli, calls }) => {
    const { code, stdout } = await cli(sync(makeHome(), "--fresh-days", "abc"));
    assert.equal(code, 1);
    assert.match(stdout, /--fresh-days/);
    assert.equal(calls.length, 0);
  });
});

test("報表模式預設 7 天：只建立 7 天內的，摘要給出「超過天數沒建立」筆數與天數、來源是預設", async () => {
  await setup(async ({ cli, created }) => {
    const { code, stdout, stderr } = await cli(sync(makeHome()));
    assert.equal(code, 0, stderr);
    assert.deepEqual(created(), ["T-1001"]);
    const report = JSON.parse(stdout);
    assert.equal(report.freshDays, 7);
    assert.equal(report.freshDaysSource, "default");
    assert.equal(report.prefsSaved, false);
    assert.equal(report.counts.stale, 2);
    assert.ok(!stdout.includes("機密標題"));
  });
});

test("--fresh-days all：報表內不論多舊都建立", async () => {
  await setup(async ({ cli, created }) => {
    const { code, stdout, stderr } = await cli(sync(makeHome(), "--fresh-days", "all"));
    assert.equal(code, 0, stderr);
    assert.deepEqual(created(), ["T-1001", "T-1002", "T-1003"]);
    const report = JSON.parse(stdout);
    assert.equal(report.freshDays, "all");
    assert.equal(report.freshDaysSource, "flag");
    assert.equal(report.counts.stale, 0);
  });
});

test("--fresh-days 30：建立 30 天內的", async () => {
  await setup(async ({ cli, created }) => {
    const { code, stdout, stderr } = await cli(sync(makeHome(), "--fresh-days", "30"));
    assert.equal(code, 0, stderr);
    assert.deepEqual(created(), ["T-1001", "T-1002"]);
    assert.equal(JSON.parse(stdout).counts.stale, 1);
  });
});

test("有期限的看期限：出現很久但期限在天數內的照樣建立", async () => {
  const soon = new Date(Date.now() + 3 * DAY).toISOString().slice(0, 10);
  await setup(async ({ cli, created }) => {
    const { code, stderr } = await cli(sync(makeHome([row("T-1001", 60, { due: soon }), row("T-1002", 60)])));
    assert.equal(code, 0, stderr);
    assert.deepEqual(created(), ["T-1001"]);
  });
});

test("prefs：set／show／clear；可重跑；寫完沒有殘留暫存檔", async () => {
  await setup(async ({ cli, stateBase }) => {
    const empty = await cli(["prefs", "show"]);
    assert.equal(empty.code, 0);
    assert.match(empty.stdout, /還沒存過/);
    for (let i = 0; i < 2; i += 1) {
      const set = await cli(["prefs", "set", "fresh-days", "all"]);
      assert.equal(set.code, 0, set.stdout);
    }
    assert.deepEqual(fs.readdirSync(stateBase).sort(), [PREFS_FILE]);
    const show = await cli(["prefs", "show", "--json"]);
    assert.deepEqual(JSON.parse(show.stdout), { saved: true, freshDays: "all", error: false });
    for (let i = 0; i < 2; i += 1) assert.equal((await cli(["prefs", "clear"])).code, 0);
    assert.equal(fs.existsSync(path.join(stateBase, PREFS_FILE)), false);
    assert.deepEqual(JSON.parse((await cli(["prefs", "show", "--json"])).stdout), { saved: false, freshDays: null, error: false });
  });
});

test("prefs：參數錯誤時結束碼 1、不寫檔", async () => {
  await setup(async ({ cli, stateBase }) => {
    for (const args of [["prefs"], ["prefs", "bogus"], ["prefs", "set", "fresh-days"], ["prefs", "set", "fresh-days", "0"], ["prefs", "set", "colour", "7"], ["prefs", "show", "extra"]]) {
      const { code } = await cli(args);
      assert.equal(code, 1, args.join(" "));
    }
    assert.deepEqual(fs.readdirSync(stateBase), []);
  });
});

test("優先序：旗標＞偏好＞預設；偏好只在報表模式採用", async () => {
  await setup(async ({ cli, created, calls }) => {
    assert.equal((await cli(["prefs", "set", "fresh-days", "30"])).code, 0);
    const home = makeHome();
    const byPref = JSON.parse((await cli(sync(home))).stdout);
    assert.equal(byPref.freshDays, 30);
    assert.equal(byPref.freshDaysSource, "prefs");
    assert.equal(byPref.prefsSaved, true);
    assert.deepEqual(created(), ["T-1001", "T-1002"]);

    calls.length = 0;
    const byFlag = JSON.parse((await cli(sync(makeHome(), "--fresh-days", "all"))).stdout);
    assert.equal(byFlag.freshDaysSource, "flag");
    assert.equal(byFlag.prefsSaved, true);
    assert.deepEqual(created(), ["T-1001", "T-1002", "T-1003"]);

    calls.length = 0;
    const noReport = JSON.parse((await cli(["sync", "--source", "daily-agenda", "--da-home", makeHome(), "--apply", "--json"])).stdout);
    assert.equal(noReport.freshDays, 7);
    assert.equal(noReport.freshDaysSource, "default");
    assert.deepEqual(created(), ["T-1001"]);
  });
});

for (const [label, content] of [["不是 JSON", "{壞掉"], ["值不合法", JSON.stringify({ version: 1, freshDays: 0 })], ["版本不符", JSON.stringify({ version: 99, freshDays: "all" })]]) {
  test(`偏好檔損壞（${label}）：退回預設 7 天、照常同步、提示一句`, async () => {
    await setup(async ({ cli, stateBase, created }) => {
      fs.writeFileSync(path.join(stateBase, PREFS_FILE), content);
      const { code, stdout, stderr } = await cli(sync(makeHome()));
      assert.equal(code, 0, stderr);
      const report = JSON.parse(stdout);
      assert.equal(report.freshDays, 7);
      assert.equal(report.freshDaysSource, "prefs-error");
      assert.equal(report.prefsSaved, false);
      assert.equal(report.prefsError, true);
      assert.deepEqual(created(), ["T-1001"]);
      const text = await cli(["sync", "--source", "daily-agenda", "--da-home", makeHome(), "--report", "R-7KQ"]);
      assert.match(text.stdout, /天數：只收 7 天內（偏好檔讀不到，這次先用 7 天）/);
      assert.ok(!text.stdout.includes("沒有存過偏好"));
      const show = await cli(["prefs", "show"]);
      assert.equal(show.code, 0);
      assert.match(show.stdout, /偏好檔讀不到或格式不對/);
      assert.deepEqual(JSON.parse((await cli(["prefs", "show", "--json"])).stdout), { saved: false, freshDays: null, error: true });
      // 帶旗標時旗標照樣優先
      const flagged = JSON.parse((await cli(sync(makeHome(), "--fresh-days", "all"))).stdout);
      assert.equal(flagged.freshDaysSource, "flag");
    });
  });
}

test("文字輸出：列出這次的天數與「超過天數沒建立」筆數，不含標題", async () => {
  await setup(async ({ cli }) => {
    const home = makeHome();
    const { code, stdout, stderr } = await cli(["sync", "--source", "daily-agenda", "--da-home", home, "--apply", "--report", "R-7KQ"]);
    assert.equal(code, 0, stderr);
    assert.match(stdout, /只收 7 天內（預設，沒有存過偏好）/);
    assert.match(stdout, /超過 7 天沒建立 2/);
    assert.ok(!stdout.includes("機密標題"));
    const all = await cli(["sync", "--source", "daily-agenda", "--da-home", home, "--report", "R-7KQ", "--fresh-days", "all"]);
    assert.match(all.stdout, /不限天數（這次指定）/);
  });
});

test("prefs set：改名失敗時清掉暫存檔、結束碼 2、不留 .tmp", async () => {
  await setup(async ({ cli, stateBase }) => {
    // 目標位置是個非空資料夾，rename 一定失敗
    fs.mkdirSync(path.join(stateBase, PREFS_FILE));
    fs.writeFileSync(path.join(stateBase, PREFS_FILE, "keep"), "x");
    const { code, stdout } = await cli(["prefs", "set", "fresh-days", "30"]);
    assert.equal(code, 2);
    assert.match(stdout, /偏好檔寫入失敗/);
    assert.deepEqual(fs.readdirSync(stateBase).filter((f) => f.endsWith(".tmp")), []);
  });
});
