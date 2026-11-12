#!/usr/bin/env node
"use strict";

// workhub-inbox 命令列入口：外部待辦 → Work Hub 個人待辦（單向、可重跑）。
// 用法：node scripts/inbox-sync.js sync --source daily-agenda [--apply] [--max-writes N]
//        [--priorities P0,P1] [--only T-1234,...|--no-create] [--exclude T-1234,...]
//        [--da-home PATH] [--review-file] [--json]
// 環境變數：WORKHUB_INBOX_ENDPOINT、WORKHUB_INBOX_API_KEY（沒設就讀 DF_WORKHUB_API_KEY）；都只從環境變數讀，金鑰不寫檔、不印出。
// 端點刻意不開放命令列指定，不能讓不可信內容有機會改掉金鑰送去的地方。
// 待辦標題與對話名稱是第三方內容：任何 stdout／stderr／--json 都不輸出，只寫進 --review-file 的本機清單檔（不進 Claude 的對話）。

const { createHash } = require("node:crypto");
const path = require("node:path");

const { AgendaSourceError, loadAgenda, resolveDaHome, taipeiDate, toInboxItems } = require("./lib/agenda.js");
const { buildCompleteArgs, buildCreateArgs, planSync, safeText } = require("./lib/plan.js");
const { ReviewFileError, buildReviewHtml, writeReviewFile } = require("./lib/review.js");
const { StateError, loadMap, mergeExisting, resolveStateDir, saveMap } = require("./lib/state.js");
const { WorkhubError, createClient, resolveApiKey, validateEndpoint } = require("./lib/workhub-client.js");

const SOURCES = new Set(["daily-agenda"]);
const VALID_PRIORITIES = new Set(["P0", "P1", "P2"]);
const PM_SOURCES = ["line", "outlook"];
const EXIT = { ok: 0, usage: 1, source: 2, partial: 3 };
const AGENDA_ID = /^T-\d{4}$/;
const SECRET_HIDDEN = "（訊息含金鑰或端點設定值，已整段隱藏；請確認 WORKHUB_INBOX_ENDPOINT 沒有和金鑰變數 WORKHUB_INBOX_API_KEY／DF_WORKHUB_API_KEY 填反）";
const BAD_RESPONSE = "回應格式不符，可能不是 Work Hub 的 MCP 端點";
const UNEXPECTED_ERROR = "UNEXPECTED_ERROR";

class UsageError extends Error {}

function parseArgs(argv) {
  const [command, ...rest] = argv;
  if (command !== "sync") throw new UsageError("用法：inbox-sync.js sync --source daily-agenda [--apply] ...");
  const opts = { source: "daily-agenda", apply: false, maxWrites: 20, priorities: ["P0", "P1", "P2"], daHome: null, json: false, reviewFile: false, only: null, exclude: [], noCreate: false };
  const takesValue = { "--source": "source", "--max-writes": "maxWrites", "--priorities": "priorities", "--da-home": "daHome", "--only": "only", "--exclude": "exclude" };
  const flags = { "--apply": "apply", "--json": "json", "--review-file": "reviewFile", "--no-create": "noCreate" };
  for (let i = 0; i < rest.length; i += 1) {
    const arg = rest[i];
    if (flags[arg]) opts[flags[arg]] = true;
    else if (takesValue[arg]) {
      const value = rest[i + 1];
      if (value === undefined || value.startsWith("--")) throw new UsageError(`${arg} 後面要接一個值`);
      opts[takesValue[arg]] = value;
      i += 1;
    } else throw new UsageError(`不認得的參數：${arg}`);
  }
  if (!SOURCES.has(opts.source)) throw new UsageError(`不支援的來源：${opts.source}（目前只有 daily-agenda）`);
  if (typeof opts.priorities === "string") opts.priorities = opts.priorities.split(",").map((p) => p.trim()).filter(Boolean);
  if (!opts.priorities.length || opts.priorities.some((p) => !VALID_PRIORITIES.has(p))) throw new UsageError("優先級只接受 P0、P1、P2");
  for (const key of ["only", "exclude"]) {
    if (typeof opts[key] !== "string") continue;
    opts[key] = opts[key].split(",").map((s) => s.trim()).filter(Boolean);
    // 空值不當成「一筆都不選」：引號一出錯就會變成這種值，不能讓它悄悄改變要寫入的範圍
    if (!opts[key].length) throw new UsageError(`--${key} 至少要一個 agenda 代碼（例 T-1234）；只想勾完成、不建立，請改用 --no-create`);
    if (opts[key].some((id) => !AGENDA_ID.test(id))) throw new UsageError(`--${key} 只接受 agenda 代碼（例 T-1234，逗號分隔）`);
  }
  if (opts.noCreate && (opts.only || opts.exclude.length)) throw new UsageError("--no-create 不能和 --only、--exclude 一起用");
  opts.maxWrites = Number(opts.maxWrites);
  if (!Number.isInteger(opts.maxWrites) || opts.maxWrites < 0) throw new UsageError("--max-writes 要是 0 以上的整數");
  return opts;
}

/**
 * 保底遮蔽：要輸出的字串若含金鑰或不合法的端點原值（兩個變數可能填反），整段換成固定文字。
 * @param {Array<string|undefined>} secrets 空值會被略過
 * @returns {(text: unknown) => string}
 */
function makeScrub(secrets) {
  const list = secrets.filter((v) => typeof v === "string" && v.length > 0);
  return (text) => {
    const str = String(text);
    return list.some((v) => str.includes(v)) ? SECRET_HIDDEN : str;
  };
}

/** 依環境變數建遮蔽器：兩個金鑰變數有值的都遮（不只實際使用的那把），加上不合法的端點原值。 */
function scrubFor(env) {
  return makeScrub([...resolveApiKey(env).secrets, endpointSecret(env.WORKHUB_INBOX_ENDPOINT)]);
}

/** 回應要長得像 Work Hub：清單要有 items 陣列、建立要有 id、勾完成要有 task.id；不像就當失敗，不報成功。 */
function expectShape(ok, tool) {
  if (!ok) throw new WorkhubError(`${tool} ${BAD_RESPONSE}`, { tool });
}

/**
 * 要輸出的錯誤文字：只用 WorkhubError 的訊息（客戶端只用代碼組成，不含伺服器自由文字）；
 * 其他例外的訊息來路不明，一律換成固定字串。
 */
function errorText(err, scrub) {
  return err instanceof WorkhubError ? safeText(scrub(err.message), 200) : UNEXPECTED_ERROR;
}

const hasId = (value) => (typeof value === "number" || typeof value === "string") && Number.isInteger(Number(value)) && Number(value) > 0;

/** 端點原值驗證不過時（例如被填成金鑰、帶帳密）才需要遮蔽；合法網址照常可以出現在輸出裡。 */
function endpointSecret(raw) {
  if (!raw) return undefined;
  try {
    validateEndpoint(raw);
    return undefined;
  } catch {
    return raw;
  }
}

async function fetchExisting(client) {
  const views = await Promise.all(["all", "completed"].map((view) => client.callTool("pm_list_personal_tasks", { view, sources: PM_SOURCES })));
  for (const result of views) expectShape(result && Array.isArray(result.items), "pm_list_personal_tasks");
  const byId = new Map();
  for (const result of views) for (const task of result.items) byId.set(String(task.id), task);
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
async function applyPlan(client, plan, maxWrites, map, scrub = String) {
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
      if (action.kind === "complete") expectShape(out && out.task && hasId(out.task.id), action.tool);
      else expectShape(out && hasId(out.id), action.tool);
      if (action.kind === "complete") {
        results.completed += 1;
        counted += 1;
        map.tasks[key] = { taskId: action.taskId, completed: true };
      } else {
        if (out.deduplicated) results.deduplicated += 1;
        else {
          results.created += 1;
          counted += 1;
        }
        map.tasks[key] = { taskId: Number(out.id), completed: false };
      }
    } catch (err) {
      counted += 1;
      results.failed += 1;
      results.failures.push({ agendaId: action.item.agendaId, action: action.kind, error: errorText(err, scrub) });
      if (err instanceof WorkhubError && AUTH_STATUS.has(err.status)) {
        results.aborted = true;
        results.notAttempted = queue.length - index - 1;
        break;
      }
    }
  }
  return results;
}

/** --only／--exclude／--no-create 只篩「建立」；完成同步不受影響。 */
function selectCreates(plan, opts) {
  const only = opts.noCreate ? new Set() : opts.only ? new Set(opts.only) : null;
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

function renderText(report, plan) {
  const c = report.counts;
  const lines = [
    `workhub-inbox ${report.mode}（來源 ${report.source}，優先級 ${report.priorities.join("/")}）`,
    `預計建立 ${c.create}、勾完成 ${c.complete}；可能已完成 ${c.maybeDone}（不寫入，請到 agenda 確認）`,
    `略過：PM 已有 ${c.exists}、優先級不符 ${c.priority}、過期或太舊 ${c.stale}、指定排除 ${c.excluded}`,
  ];
  if (plan.create.length) {
    lines.push("", "預計建立：");
    // 只列代碼、優先級、期限與來源類別（LINE／信件／行事曆，是程式自己的分類）；標題與對話名稱只在清單檔
    for (const i of plan.create) lines.push(`- ${i.agendaId}  ${i.agendaPriority}  期限 ${i.due || "—"}  ${i.sourceLabel}`);
  }
  if (report.reviewFile) {
    lines.push("", `標題與來源寫在試跑清單檔（請使用者自己打開看）：${safeText(report.reviewFile, 1000)}`);
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
  const env = deps.env || process.env;
  const rawEndpoint = env.WORKHUB_INBOX_ENDPOINT;
  const { apiKey, apiKeyVar } = resolveApiKey(env);
  const scrub = scrubFor(env);
  const rawPrint = deps.print || ((s) => process.stdout.write(`${s}\n`));
  let opts;
  // 最後一道：整份輸出若仍含金鑰或端點原值就不印原文（各錯誤欄位已先逐欄遮蔽，正常不會走到這裡）
  const print = (s) => {
    const text = String(s);
    if (scrub(text) === text) rawPrint(text);
    else rawPrint(opts && opts.json ? JSON.stringify({ error: SECRET_HIDDEN }) : SECRET_HIDDEN);
  };
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
    client = deps.client || createClient({ endpoint: rawEndpoint, apiKey, apiKeyVar, fetchImpl: deps.fetchImpl });
  } catch (err) {
    print(scrub(err.message));
    return EXIT.usage;
  }

  const baseDir = resolveStateDir({ env });
  const stateDir = deps.stateDir || stateDirFor(baseDir, rawEndpoint, apiKey);
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
    print(`讀取 Work Hub 現況失敗：${errorText(err, scrub)}`);
    return err instanceof WorkhubError && AUTH_STATUS.has(err.status) ? EXIT.usage : EXIT.partial;
  }

  const existing = mergeExisting(listed, map);
  const plan = selectCreates(planSync({ items, existing, today: taipeiDate(deps.now || new Date()), priorities: opts.priorities }), opts);
  const report = summarize(opts, plan);
  if (opts.reviewFile) {
    // 先寫清單檔再寫入 PM：清單檔寫不出來就整個停下，不送任何寫入
    try {
      report.reviewFile = writeReviewFile(deps.reviewDir || baseDir, buildReviewHtml(plan, taipeiDate(deps.now || new Date())));
    } catch (err) {
      if (!(err instanceof ReviewFileError)) throw err;
      print(err.message);
      return EXIT.source;
    }
  }
  if (opts.apply) {
    // 清單看得到的一律覆寫對照檔（清單為準），日後掉出 500 筆範圍仍查得到 task_id 與正確狀態
    for (const task of listed) {
      map.tasks[task.external_id] = { taskId: Number(task.id), completed: task.workflow_state === "completed" };
    }
    try {
      report.results = await applyPlan(client, plan, opts.maxWrites, map, scrub);
    } finally {
      saveMap(stateDir, map);
    }
  }
  print(opts.json ? JSON.stringify(report, null, 2) : renderText(report, plan));
  if (report.results && report.results.aborted) return EXIT.usage;
  return report.results && report.results.failed > 0 ? EXIT.partial : EXIT.ok;
}

if (require.main === module) {
  main(process.argv.slice(2)).then((code) => { process.exitCode = code; }, (err) => {
    const scrub = scrubFor(process.env);
    process.stderr.write(`workhub-inbox 未預期錯誤：${safeText(scrub(err && err.message), 300)}\n`);
    process.exitCode = 1;
  });
}

module.exports = { main, parseArgs, stateDirFor };
