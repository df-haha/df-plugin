"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");

const { planSync, buildCreateArgs, idempotencyKey } = require("../scripts/lib/plan.js");

const TODAY = "2026-11-10";

const PRIORITY = { P0: 1, P1: 2, P2: 3 };

function item(overrides = {}) {
  const base = {
    agendaId: "T-0001",
    externalId: "agenda:T-0001:2026-11-09T07:30:00+08:00",
    source: "line",
    sourceLabel: "LINE",
    sourceName: "LINE・某群組",
    title: "確認報價",
    agendaPriority: "P1",
    priority: 2,
    due: null,
    status: "open",
    createdDate: "2026-11-09",
    ...overrides,
  };
  return { ...base, priority: overrides.priority ?? PRIORITY[base.agendaPriority] };
}

function plan(items, existing = [], opts = {}) {
  return planSync({ items, existing, today: TODAY, priorities: ["P0", "P1", "P2"], ...opts });
}

test("建立：open、無期限、7 天內建立且 PM 沒有 → 建立", () => {
  const result = plan([item()]);
  assert.equal(result.create.length, 1);
  assert.equal(result.complete.length, 0);
});

test("新鮮度：無期限待辦以建立日判斷，剛好 7 天前收、8 天前不收", () => {
  const result = plan([
    item({ agendaId: "T-0007", externalId: "a7", createdDate: "2026-11-03" }),
    item({ agendaId: "T-0008", externalId: "a8", createdDate: "2026-11-02" }),
  ]);
  assert.deepEqual(result.create.map((i) => i.agendaId), ["T-0007"]);
  assert.equal(result.skipped.stale, 1);
});

test("新鮮度：有期限待辦以期限判斷，建立日再舊也照期限", () => {
  const result = plan([
    item({ agendaId: "T-0101", externalId: "b1", due: "2026-11-03", createdDate: "2026-10-01" }),
    item({ agendaId: "T-0102", externalId: "b2", due: "2026-11-02", createdDate: "2026-11-09" }),
    item({ agendaId: "T-0103", externalId: "b3", due: "2026-12-01", createdDate: "2026-09-01" }),
  ]);
  assert.deepEqual(result.create.map((i) => i.agendaId).sort(), ["T-0101", "T-0103"]);
  assert.equal(result.skipped.stale, 1);
});

test("優先級過濾：首批只收 P0、P1", () => {
  const result = plan(
    [item({ agendaId: "T-1", externalId: "p0", agendaPriority: "P0" }),
     item({ agendaId: "T-2", externalId: "p2", agendaPriority: "P2" })],
    [],
    { priorities: ["P0", "P1"] },
  );
  assert.deepEqual(result.create.map((i) => i.agendaId), ["T-1"]);
  assert.equal(result.skipped.priority, 1);
});

test("PM 已有同 external_id → 不重建", () => {
  const existing = [{ id: 9, source: "line", external_id: item().externalId, workflow_state: "active", completed_at: null }];
  const result = plan([item()], existing);
  assert.equal(result.create.length, 0);
  assert.equal(result.skipped.exists, 1);
});

test("agenda 標 done 或 dismissed → PM 未完成的勾完成，已完成的不重複", () => {
  const items = [
    item({ agendaId: "T-D", externalId: "d", status: "done" }),
    item({ agendaId: "T-X", externalId: "x", status: "dismissed" }),
    item({ agendaId: "T-C", externalId: "c", status: "done" }),
  ];
  const existing = [
    { id: 1, source: "line", external_id: "d", workflow_state: "active", completed_at: null },
    { id: 2, source: "line", external_id: "x", workflow_state: "waiting", completed_at: null },
    { id: 3, source: "line", external_id: "c", workflow_state: "completed", completed_at: "2026-11-09T00:00:00Z" },
  ];
  const result = plan(items, existing);
  assert.deepEqual(result.complete.map((c) => c.taskId).sort(), [1, 2]);
  assert.equal(result.create.length, 0);
});

test("done 但 PM 沒有 → 不建立也不完成", () => {
  const result = plan([item({ status: "done" })]);
  assert.equal(result.create.length, 0);
  assert.equal(result.complete.length, 0);
});

test("PM 已取消的待辦不再被勾完成", () => {
  const existing = [{ id: 5, source: "line", external_id: "z", workflow_state: "cancelled", completed_at: null }];
  const result = plan([item({ externalId: "z", status: "done" })], existing);
  assert.equal(result.complete.length, 0);
});

test("maybe_done／collapsed 只列入摘要，不寫入", () => {
  const existing = [{ id: 1, source: "line", external_id: "m", workflow_state: "active", completed_at: null }];
  const result = plan([item({ externalId: "m", status: "maybe_done" }), item({ externalId: "n", status: "collapsed" })], existing);
  assert.equal(result.maybeDone.length, 2);
  assert.equal(result.create.length, 0);
  assert.equal(result.complete.length, 0);
});

test("建立順序：優先級高的先、同級期限近的先、無期限排後", () => {
  const result = plan([
    item({ agendaId: "a", externalId: "a", agendaPriority: "P1", due: null }),
    item({ agendaId: "b", externalId: "b", agendaPriority: "P1", due: "2026-11-12" }),
    item({ agendaId: "c", externalId: "c", agendaPriority: "P0", due: "2026-11-20" }),
  ]);
  assert.deepEqual(result.create.map((i) => i.agendaId), ["c", "b", "a"]);
});

test("planSync 不改動傳入的陣列", () => {
  const items = [item()];
  const snapshot = JSON.stringify(items);
  plan(items);
  assert.equal(JSON.stringify(items), snapshot);
});

test("buildCreateArgs：全天期限、來源、標籤、說明，且不帶提醒", () => {
  const args = buildCreateArgs(item({ due: "2026-11-12", agendaPriority: "P0", priority: 1 }));
  assert.equal(args.source, "line");
  assert.equal(args.external_id, item().externalId);
  assert.equal(args.priority, 1);
  // PM 的 zod datetime 只收 UTC（Z 結尾）：台北 11/12 00:00 ＝ UTC 11/11 16:00
  assert.equal(args.due_at, "2026-11-11T16:00:00.000Z");
  assert.equal(args.due_has_time, false);
  assert.deepEqual(args.labels, ["LINE"]);
  assert.equal(args.workflow_state, "active");
  assert.match(args.description, /T-0001/);
  assert.match(args.description, /LINE・某群組/);
  assert.match(args.description, /由 AI 整理自外部訊息/);
  assert.equal(args.reminders, undefined);
  assert.match(args.idempotency_key, /^workhub-inbox:create:[0-9a-f]{32}$/);
});

test("buildCreateArgs：無期限不帶 due_at，標題壓空白並截到 300 字", () => {
  const args = buildCreateArgs(item({ title: `  第一行\n第二行  ${"字".repeat(400)}` }));
  assert.equal(args.due_at, undefined);
  assert.ok(!args.title.includes("\n"));
  assert.ok(args.title.startsWith("第一行 第二行"));
  assert.equal([...args.title].length, 300);
});

test("idempotencyKey：同動作同鍵穩定，不同動作不同", () => {
  assert.equal(idempotencyKey("create", "k"), idempotencyKey("create", "k"));
  assert.notEqual(idempotencyKey("create", "k"), idempotencyKey("complete", "k"));
});
