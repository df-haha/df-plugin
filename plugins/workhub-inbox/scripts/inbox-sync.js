#!/usr/bin/env node
"use strict";

// workhub-inbox 命令列入口：外部待辦 → Work Hub 個人待辦（單向、可重跑）。
// 用法：node scripts/inbox-sync.js sync --source daily-agenda [--apply] [--max-writes N]
//        [--priorities P0,P1] [--endpoint URL] [--da-home PATH] [--show-titles] [--json]
// 環境變數：WORKHUB_INBOX_ENDPOINT、WORKHUB_INBOX_API_KEY（金鑰只從環境變數讀，不寫檔、不印出）。

const { AgendaSourceError, loadAgenda, resolveDaHome, taipeiDate, toInboxItems } = require("./lib/agenda.js");
const { buildCompleteArgs, buildCreateArgs, planSync } = require("./lib/plan.js");
const { WorkhubError, createClient } = require("./lib/workhub-client.js");

const SOURCES = new Set(["daily-agenda"]);
const VALID_PRIORITIES = new Set(["P0", "P1", "P2"]);
const PM_SOURCES = ["line", "outlook"];
const EXIT = { ok: 0, usage: 1, source: 2, partial: 3 };

class UsageError extends Error {}

function parseArgs(argv) {
  const [command, ...rest] = argv;
  if (command !== "sync") throw new UsageError("用法：inbox-sync.js sync --source daily-agenda [--apply] ...");
  const opts = { source: "daily-agenda", apply: false, maxWrites: 20, priorities: ["P0", "P1", "P2"], endpoint: null, daHome: null, json: false, showTitles: false };
  const takesValue = { "--source": "source", "--max-writes": "maxWrites", "--priorities": "priorities", "--endpoint": "endpoint", "--da-home": "daHome" };
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

async function applyPlan(client, plan, maxWrites) {
  const queue = [
    ...plan.complete.map((c) => ({ kind: "complete", tool: "pm_complete_personal_task", args: buildCompleteArgs(c), item: c.item })),
    ...plan.create.map((item) => ({ kind: "create", tool: "pm_create_personal_task", args: buildCreateArgs(item), item })),
  ];
  const results = { created: 0, deduplicated: 0, completed: 0, failed: 0, deferred: Math.max(0, queue.length - maxWrites), failures: [] };
  for (const action of queue.slice(0, maxWrites)) {
    try {
      const out = await client.callTool(action.tool, action.args);
      if (action.kind === "complete") results.completed += 1;
      else if (out && out.deduplicated) results.deduplicated += 1;
      else results.created += 1;
    } catch (err) {
      results.failed += 1;
      results.failures.push({ agendaId: action.item.agendaId, action: action.kind, error: String(err.message).slice(0, 200) });
    }
  }
  return results;
}

function summarize(opts, plan) {
  return {
    mode: opts.apply ? "apply" : "dry-run",
    source: opts.source,
    priorities: opts.priorities,
    counts: { create: plan.create.length, complete: plan.complete.length, maybeDone: plan.maybeDone.length, ...plan.skipped },
    create: plan.create.map((i) => ({ agendaId: i.agendaId, priority: i.agendaPriority, due: i.due, source: i.sourceLabel })),
    complete: plan.complete.map((c) => ({ agendaId: c.item.agendaId, taskId: c.taskId })),
  };
}

function renderText(report, plan, opts) {
  const c = report.counts;
  const lines = [
    `workhub-inbox ${report.mode}（來源 ${report.source}，優先級 ${report.priorities.join("/")}）`,
    `預計建立 ${c.create}、勾完成 ${c.complete}；可能已完成 ${c.maybeDone}（不寫入，請到 agenda 確認）`,
    `略過：PM 已有 ${c.exists}、優先級不符 ${c.priority}、過期或太舊 ${c.stale}`,
  ];
  if (plan.create.length) {
    lines.push("", "預計建立：");
    for (const i of plan.create) {
      const title = opts.showTitles ? `  ${i.title}` : "";
      lines.push(`- ${i.agendaId}  ${i.agendaPriority}  期限 ${i.due || "—"}  ${i.sourceName}${title}`);
    }
  }
  if (report.results) {
    const r = report.results;
    lines.push("", `寫入結果：建立 ${r.created}、已存在 ${r.deduplicated}、勾完成 ${r.completed}、失敗 ${r.failed}、超過上限未執行 ${r.deferred}`);
    for (const f of r.failures) lines.push(`- 失敗 ${f.agendaId}（${f.action}）：${f.error}`);
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

  let client;
  try {
    client = deps.client || createClient({ endpoint: opts.endpoint || env.WORKHUB_INBOX_ENDPOINT, apiKey: env.WORKHUB_INBOX_API_KEY });
  } catch (err) {
    print(err.message);
    return EXIT.usage;
  }

  let existing;
  try {
    existing = await fetchExisting(client);
  } catch (err) {
    print(`讀取 Work Hub 現況失敗：${err.message}`);
    return err instanceof WorkhubError && [401, 403].includes(err.status) ? EXIT.usage : EXIT.partial;
  }

  const plan = planSync({ items, existing, today: taipeiDate(deps.now || new Date()), priorities: opts.priorities });
  const report = summarize(opts, plan);
  if (opts.apply) report.results = await applyPlan(client, plan, opts.maxWrites);
  print(opts.json ? JSON.stringify(report, null, 2) : renderText(report, plan, opts));
  return report.results && report.results.failed > 0 ? EXIT.partial : EXIT.ok;
}

if (require.main === module) {
  main(process.argv.slice(2)).then((code) => { process.exitCode = code; }, (err) => {
    process.stderr.write(`workhub-inbox 未預期錯誤：${err.message}\n`);
    process.exitCode = 1;
  });
}

module.exports = { main, parseArgs };
