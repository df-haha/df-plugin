"use strict";

// 本機對照檔：去重鍵 → Work Hub 待辦 ID。
// 原因：pm_list_personal_tasks 每個檢視最多回 500 筆，累積久了清單查不到舊待辦，勾完成就找不到 task_id。
// 寫入先寫 .tmp 再 rename，中途掛掉不會留下半個檔案。

const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const MAP_FILE = "agenda-map.json";
const VERSION = 1;

class StateError extends Error {
  constructor(message) {
    super(message);
    this.name = "StateError";
  }
}

function resolveStateDir({ explicit, env = process.env } = {}) {
  return explicit || env.WORKHUB_INBOX_STATE_DIR || path.join(os.homedir(), ".workhub-inbox");
}

/** 讀對照檔；不存在回空表，壞掉直接報錯（不默默當成空表，以免重複動作）。 */
function loadMap(dir) {
  const file = path.join(dir, MAP_FILE);
  let raw;
  try {
    raw = fs.readFileSync(file, "utf8");
  } catch (err) {
    if (err.code === "ENOENT") return { version: VERSION, tasks: {} };
    throw new StateError(`讀不到對照檔 ${file}（${err.code}）`);
  }
  let data;
  try {
    data = JSON.parse(raw);
  } catch {
    throw new StateError(`對照檔 ${file} 不是有效的 JSON，請檢查或移走後重跑`);
  }
  if (data.version !== VERSION || typeof data.tasks !== "object" || data.tasks === null) {
    throw new StateError(`對照檔 ${file} 格式不符（version ${data.version}）`);
  }
  return data;
}

function saveMap(dir, map) {
  fs.mkdirSync(dir, { recursive: true, mode: 0o700 });
  const file = path.join(dir, MAP_FILE);
  const tmp = `${file}.${process.pid}.tmp`;
  fs.writeFileSync(tmp, `${JSON.stringify(map, null, 2)}\n`, { encoding: "utf8", mode: 0o600 });
  fs.renameSync(tmp, file);
}

/** 清單結果為準；清單查不到的，用對照檔補成「推定狀態」的既有待辦。 */
function mergeExisting(listed, map) {
  const seen = new Set(listed.map((t) => t.external_id));
  const fromMap = Object.entries(map.tasks)
    .filter(([key]) => !seen.has(key))
    .map(([key, entry]) => ({
      id: entry.taskId,
      external_id: key,
      workflow_state: entry.completed ? "completed" : "active",
      completed_at: null,
    }));
  return [...listed, ...fromMap];
}

module.exports = { StateError, loadMap, mergeExisting, resolveStateDir, saveMap };
