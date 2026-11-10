"use strict";

// 純函式：收件匣項目＋Work Hub 現況 → 要做的動作（讀現況→比對目標→只補差額，可重跑）。

const { createHash } = require("node:crypto");

const FRESH_DAYS = 7;
const TITLE_MAX = 300;
const DONE_STATES = new Set(["done", "dismissed"]);
const MAYBE_DONE_STATES = new Set(["maybe_done", "collapsed"]);
const CLOSED_PM_STATES = new Set(["completed", "cancelled"]);
const FAR_FUTURE = "9999-12-31";

function shiftDate(isoDate, days) {
  const d = new Date(`${isoDate}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

function isFresh(item, cutoff) {
  return (item.due || item.createdDate) >= cutoff;
}

function isClosed(task) {
  return CLOSED_PM_STATES.has(task.workflow_state) || Boolean(task.completed_at);
}

function byPriorityThenDue(a, b) {
  return a.priority - b.priority || (a.due || FAR_FUTURE).localeCompare(b.due || FAR_FUTURE);
}

/**
 * @param {{items: object[], existing: object[], today: string, priorities: string[], freshDays?: number}} input
 * @returns {{create: object[], complete: {item: object, taskId: number}[], maybeDone: object[],
 *            skipped: {stale: number, priority: number, exists: number}}}
 */
function planSync({ items, existing, today, priorities, freshDays = FRESH_DAYS }) {
  const byExternalId = new Map(existing.filter((t) => t.external_id).map((t) => [t.external_id, t]));
  const allowed = new Set(priorities);
  const cutoff = shiftDate(today, -freshDays);
  const create = [];
  const complete = [];
  const maybeDone = [];
  const skipped = { stale: 0, priority: 0, exists: 0 };

  for (const item of items) {
    const task = byExternalId.get(item.externalId);
    if (DONE_STATES.has(item.status)) {
      if (task && !isClosed(task)) complete.push({ item, taskId: Number(task.id) });
      continue;
    }
    if (MAYBE_DONE_STATES.has(item.status)) {
      maybeDone.push(item);
      continue;
    }
    if (item.status !== "open") continue;
    if (task) skipped.exists += 1;
    else if (!allowed.has(item.agendaPriority)) skipped.priority += 1;
    else if (!isFresh(item, cutoff)) skipped.stale += 1;
    else create.push(item);
  }
  return { create: [...create].sort(byPriorityThenDue), complete, maybeDone, skipped };
}

function idempotencyKey(action, externalId) {
  return `workhub-inbox:${action}:${createHash("sha256").update(externalId).digest("hex").slice(0, 32)}`;
}

function cleanTitle(title) {
  const flat = String(title).split(/\s+/).filter(Boolean).join(" ") || "（無標題）";
  return [...flat].slice(0, TITLE_MAX).join("");
}

/** 收件匣項目 → pm_create_personal_task 參數。v1 不設提醒。 */
function buildCreateArgs(item) {
  const args = {
    idempotency_key: idempotencyKey("create", item.externalId),
    title: cleanTitle(item.title),
    description: `來源：${item.sourceName}｜daily-agenda 代碼 ${item.agendaId}\n由 AI 整理自外部訊息，內容僅供參考。`,
    priority: item.priority,
    workflow_state: "active",
    source: item.source,
    external_id: item.externalId,
    labels: [item.sourceLabel],
  };
  if (item.due) {
    args.due_at = new Date(`${item.due}T00:00:00+08:00`).toISOString();
    args.due_has_time = false;
  }
  return args;
}

function buildCompleteArgs({ item, taskId }) {
  return { idempotency_key: idempotencyKey("complete", item.externalId), task_id: taskId };
}

module.exports = { buildCompleteArgs, buildCreateArgs, idempotencyKey, planSync };
