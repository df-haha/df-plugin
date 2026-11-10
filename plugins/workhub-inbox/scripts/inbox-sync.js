#!/usr/bin/env node
"use strict";

// workhub-inbox 命令列入口：外部待辦 → Work Hub 個人待辦（單向、可重跑）。
// 用法：node scripts/inbox-sync.js sync --source daily-agenda [--apply] [--max-writes N]
//        [--priorities P0,P1] [--endpoint URL] [--da-home PATH] [--show-titles] [--json]
// 環境變數：WORKHUB_INBOX_ENDPOINT、WORKHUB_INBOX_API_KEY（金鑰只從環境變數讀，不寫檔、不印出）。

const { createHash } = require("node:crypto");
const path = require("node:path");

const { AgendaSourceError, loadAgenda, resolveDaHome, taipeiDate, toInboxItems } = require("./lib/agenda.js");
const { buildCompleteArgs, buildCreateArgs, planSync, safeText } = require("./lib/plan.js");
const { StateError, loadMap, mergeExisting, resolveStateDir, saveMap } = require("./lib/state.js");
const { WorkhubError, createClient } = require("./lib/workhub-client.js");

const SOURCES = new Set(["daily-agenda"]);
const VALID_PRIORITIES = new Set(["P0", "P1", "P2"]);
const PM_SOURCES = ["line", "outlook"];
const EXIT = { ok: 0, usage: 1, source: 2, partial: 3 };
const AGENDA_ID = /^T-\d{4}$/;

class UsageError extends Error {}

function parseArgs(argv) {
  const [command, ...rest] = argv;
  if (command !== "sync") throw new UsageError("用法：inbox-sync.js sync --source daily-agenda [--apply] ...");
  const opts = { source: "daily-agenda", apply: false, maxWrites: 20, priorities: ["P0", "P1", "P2"], endpoint: null, daHome: null, json: false, showTitles: false, only: null, exclude: [] };
  const takesValue = { "--source": "source", "--max-writes": "maxWrites", "--priorities": "priorities", "--endpoint": "endpoint", "--da-home": "daHome", "--only": "only", "--exclude": "exclude" };
  const flags = { "--apply": "apply", "--json": "json", "--show-titles": "showTitles" };
  for (let i = 0; i < rest.length; i += 1) {
    const arg = rest[i];
    if (flags[arg]) opts[flags[arg]] = true;
    else if (takesValue[arg] && i + 1 < rest.length) opts[takesValue[arg]] = rest[++i];
    else throw new UsageError(`不認得的參數：${arg}`);
  }
  if (!SOURCES.has(opts.source)) throw new UsageError(`不支援的來源：${opts.source}（目前只有 daily-agenda）`);
  if (typeof opts.priorities === "string") opts.priorities = opts.priorities.split(",").map((p) => p.trim()).filter(Boolean);
  if (!opts.priorities.length || opts.priorities.some((p) => !VALID_PRIORITIES.has(p))) throw new UsageError("優先級只接受 P0、P1、P2");
  for (const key of ["only", "exclude"]) {
    if (typeof opts[key] !== "string") continue;
    opts[key] = opts[key].split(",").map((s) => s.trim()).filter(Boolean);
    if (opts[key].some((id) => !AGENDA_ID.test(id))) throw new UsageError(`--${key} 只接受 agenda 代碼（例 T-1234，逗號分隔）`);
  }
  opts.maxWrites = Number(opts.maxWrites);
  if (!Number.isInteger(opts.maxWrites) || opts.maxWrites < 0) throw new UsageError("--max-writes 要是 0 以上的整數");
  return opts;
}

async function fetchExisting(client) {
  const views = await Promise.all(["all", "completed"].map((view) => client.callTool("pm_list_personal_tasks", { view, sources: PM_SOURCES })));
  const byId = new Map();
  for (const result of views) for (const task of (result && result.items) || []) byId.set(String(task.id), task);
  return [...byId.values()].filter((t) => String(t.external_id || "").startsWith("agenda:"));
}

const AUTH_STATUS = new Set([401, 403]);

/**
 * 對照檔依「端點＋金鑰」分資料夾：不同環境或不同使用者的待辦 ID 不能混用。
 * 只取不可逆雜湊的前 16 碼當資料夾名，不存金鑰本身。換金鑰會開新的空表（伺服器去重仍防重複建立）。
 */
function stateDirFor(base, endpoint, apiKey) {
  const digest = createHash("sha256").update(`workhub-inbox\n${endpoint || ""}\n${apiKey || ""}`).digest("hex").slice(0, 16);
  return path.join(base, `scope-${digest}`);
}

/**
 * 依序寫入，並把結果記進對照檔 *map*（原地更新，呼叫端負責存檔）。
 * 上限計算：每個送出的寫入都算一筆（失敗也算，因為結果不確定時伺服器可能已寫入）；
 * 只有確認是去重命中（同鍵的舊待辦，含已取消）才不算。遇到 401／403 立即停止。
 */
async function applyPlan(client, plan, maxWrites, map) {
  const queue = [
    ...plan.complete.map((c) => ({ kind: "complete", tool: "pm_complete_personal_task", args: buildCompleteArgs(c), item: c.item, taskId: c.taskId })),
    ...plan.create.map((item) => ({ kind: "create", tool: "pm_create_personal_task", args: buildCreateArgs(item), item })),
  ];
  // deferred＝因上限延到下次；notAttempted＝因金鑰問題中止而沒送出
  const results = { created: 0, deduplicated: 0, completed: 0, failed: 0, deferred: 0, notAttempted: 0, aborted: false, failures: [] };
  let counted = 0;
  for (const [index, action] of queue.entries()) {
    if (counted >= maxWrites) {
      results.deferred = queue.length - index;
      break;
    }
    const key = action.item.externalId;
    try {
      const out = await client.callTool(action.tool, action.args);
      if (action.kind === "complete") {
        results.completed += 1;
        counted += 1;
        map.tasks[key] = { taskId: action.taskId, completed: true };
      } else {
        if (out && out.deduplicated) results.deduplicated += 1;
        else {
          results.created += 1;
          counted += 1;
        }
        if (out && out.id) map.tasks[key] = { taskId: Number(out.id), completed: false };
      }
    } catch (err) {
      counted += 1;
      results.failed += 1;
      results.failures.push({ agendaId: action.item.agendaId, action: action.kind, error: String(err.message).slice(0, 200) });
      if (err instanceof WorkhubError && AUTH_STATUS.has(err.status)) {
        results.aborted = true;
        results.notAttempted = queue.length - index - 1;
        break;
      }
    }
  }
  return results;
}

/** --only／--exclude 只篩「建立」；完成同步不受影響。 */
function selectCreates(plan, opts) {
  const only = opts.only ? new Set(opts.only) : null;
  const exclude = new Set(opts.exclude);
  const create = plan.create.filter((i) => (!only || only.has(i.agendaId)) && !exclude.has(i.agendaId));
  return { ...plan, create, excluded: plan.create.length - create.length };
}

function summarize(opts, plan) {
  return {
    mode: opts.apply ? "apply" : "dry-run",
    source: opts.source,
    priorities: opts.priorities,
    counts: { create: plan.create.length, complete: plan.complete.length, maybeDone: plan.maybeDone.length, excluded: plan.excluded, ...plan.skipped },
    create: plan.create.map((i) => ({ agendaId: i.agendaId, priority: i.agendaPriority, due: i.due, source: i.sourceLabel })),
    complete: plan.complete.map((c) => ({ agendaId: c.item.agendaId, taskId: c.taskId })),
  };
}

function renderText(report, plan, opts) {
  const c = report.counts;
  const lines = [
    `workhub-inbox ${report.mode}（來源 ${report.source}，優先級 ${report.priorities.join("/")}）`,
    `預計建立 ${c.create}、勾完成 ${c.complete}；可能已完成 ${c.maybeDone}（不寫入，請到 agenda 確認）`,
    `略過：PM 已有 ${c.exists}、優先級不符 ${c.priority}、過期或太舊 ${c.stale}、指定排除 ${c.excluded}`,
  ];
  if (plan.create.length) {
    lines.push("", opts.showTitles ? "預計建立（標題與對話名稱是外部內容，只供檢視）：" : "預計建立：");
    for (const i of plan.create) {
      // 標題與對話名稱是第三方內容：去控制字元、截長，標題用引號包住，避免被當成輸出的一部分或指令。
      const where = opts.showTitles ? safeText(i.sourceName, 60) : i.sourceLabel;
      const title = opts.showTitles ? `  ${JSON.stringify(safeText(i.title, 200))}` : "";
      lines.push(`- ${i.agendaId}  ${i.agendaPriority}  期限 ${i.due || "—"}  ${where}${title}`);
    }
  }
  if (report.results) {
    const r = report.results;
    lines.push("", `寫入結果：建立 ${r.created}、已存在 ${r.deduplicated}、勾完成 ${r.completed}、失敗 ${r.failed}、超過上限未執行 ${r.deferred}`);
    for (const f of r.failures) lines.push(`- 失敗 ${f.agendaId}（${f.action}）：${f.error}`);
    if (r.aborted) lines.push(`⚠️ 金鑰無效或沒有寫入權限，已停止；中止後未執行 ${r.notAttempted} 筆。`);
    if (r.deferred > 0) lines.push(`⚠️ 有 ${r.deferred} 筆超過上限，下次執行再補；若一直補不完，出現超過 7 天的會被略過，必要時調高 --max-writes。`);
  }
  return lines.join("\n");
}

/**
 * @param {string[]} argv
 * @param {{client?: object, daHome?: string, now?: Date, print?: (s: string) => void, env?: object}} deps
 * @returns {Promise<number>} 結束碼
 */
async function main(argv, deps = {}) {
  const print = deps.print || ((s) => process.stdout.write(`${s}\n`));
  const env = deps.env || process.env;
  let opts;
  try {
    opts = parseArgs(argv);
  } catch (err) {
    print(err.message);
    return EXIT.usage;
  }

  let items;
  try {
    const home = deps.daHome || resolveDaHome({ explicit: opts.daHome, env });
    const { ledger, state } = loadAgenda(home);
    items = toInboxItems(ledger, state);
  } catch (err) {
    if (!(err instanceof AgendaSourceError)) throw err;
    print(err.message);
    return EXIT.source;
  }

  const endpoint = opts.endpoint || env.WORKHUB_INBOX_ENDPOINT;
  let client;
  try {
    client = deps.client || createClient({ endpoint, apiKey: env.WORKHUB_INBOX_API_KEY });
  } catch (err) {
    print(err.message);
    return EXIT.usage;
  }

  const stateDir = deps.stateDir || stateDirFor(resolveStateDir({ env }), endpoint, env.WORKHUB_INBOX_API_KEY);
  let map;
  try {
    map = loadMap(stateDir);
  } catch (err) {
    if (!(err instanceof StateError)) throw err;
    print(err.message);
    return EXIT.source;
  }

  let listed;
  try {
    listed = await fetchExisting(client);
  } catch (err) {
    print(`讀取 Work Hub 現況失敗：${err.message}`);
    return err instanceof WorkhubError && AUTH_STATUS.has(err.status) ? EXIT.usage : EXIT.partial;
  }

  const existing = mergeExisting(listed, map);
  const plan = selectCreates(planSync({ items, existing, today: taipeiDate(deps.now || new Date()), priorities: opts.priorities }), opts);
  const report = summarize(opts, plan);
  if (opts.apply) {
    // 清單看得到的一律覆寫對照檔（清單為準），日後掉出 500 筆範圍仍查得到 task_id 與正確狀態
    for (const task of listed) {
      map.tasks[task.external_id] = { taskId: Number(task.id), completed: task.workflow_state === "completed" };
    }
    try {
      report.results = await applyPlan(client, plan, opts.maxWrites, map);
    } finally {
      saveMap(stateDir, map);
    }
  }
  print(opts.json ? JSON.stringify(report, null, 2) : renderText(report, plan, opts));
  if (report.results && report.results.aborted) return EXIT.usage;
  return report.results && report.results.failed > 0 ? EXIT.partial : EXIT.ok;
}

if (require.main === module) {
  main(process.argv.slice(2)).then((code) => { process.exitCode = code; }, (err) => {
    process.stderr.write(`workhub-inbox 未預期錯誤：${err.message}\n`);
    process.exitCode = 1;
  });
}

module.exports = { main, parseArgs, stateDirFor };
