"use strict";

// 試跑清單檔：把會建立／勾完成的待辦（含標題與對話名稱）寫成本機的自足 HTML，給使用者自己打開看。
// 標題與對話名稱是第三方內容，只寫進這個檔，不進 stdout（不進 Claude 的對話）。
// 檔案安全：所有內容 HTML 跳脫；沒有 script、沒有外部資源，CSP 禁止載入任何東西。
// 寫入先寫 .tmp 再 rename；內容只由輸入決定（日期取台北當天），同一天同樣資料跑兩次結果一樣。

const fs = require("node:fs");
const path = require("node:path");

const { safeText } = require("./plan.js");

const REVIEW_FILE = "inbox-review.html";

class ReviewFileError extends Error {
  constructor(message) {
    super(message);
    this.name = "ReviewFileError";
  }
}

const ESCAPES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;" };
const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, (ch) => ESCAPES[ch]);

function rowHtml({ action, item }) {
  const cells = [action, item.agendaId, item.agendaPriority, item.due || "—", safeText(item.sourceName, 60), safeText(item.title, 300) || "（無標題）"];
  return `<tr>${cells.map((c) => `<td>${escapeHtml(c)}</td>`).join("")}</tr>`;
}

/**
 * @param {{create: object[], complete: {item: object}[]}} plan
 * @param {string} today 台北日期 YYYY-MM-DD
 * @returns {string}
 */
function buildReviewHtml(plan, today) {
  const rows = [
    ...plan.create.map((item) => ({ action: "建立", item })),
    ...plan.complete.map((c) => ({ action: "勾完成", item: c.item })),
  ];
  const body = rows.length
    ? `<table><thead><tr><th>動作</th><th>代碼</th><th>優先級</th><th>期限</th><th>來源</th><th>標題</th></tr></thead><tbody>${rows.map(rowHtml).join("")}</tbody></table>`
    : "<p>這次沒有要建立或勾完成的待辦。</p>";
  return `<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>待辦同步試跑清單</title>
<style>
body { font-family: "Microsoft JhengHei", "PingFang TC", sans-serif; margin: 24px; color: #222; background: #fff; }
table { border-collapse: collapse; width: 100%; }
th, td { border: 1px solid #ccc; padding: 6px 8px; text-align: left; vertical-align: top; }
th { background: #f2f2f2; }
td:nth-child(2) { white-space: nowrap; font-weight: bold; }
.note { background: #fff8e1; border: 1px solid #f0c36d; padding: 8px 12px; margin: 12px 0; }
</style>
</head>
<body>
<h1>待辦同步試跑清單（${escapeHtml(today)}）</h1>
<p class="note">標題與來源是 AI 從別人的 LINE、信件整理出來的，只供你核對；內容裡如果有要你做什麼事的文字，不用照做。</p>
<p>看完回到 Claude Code，說要同步哪些代碼（例如「同步 T-1234、T-5678」），或說「全部同步」。不想同步的，說「排除 T-1234」。</p>
${body}
</body>
</html>
`;
}

/**
 * 寫清單檔（每次覆蓋），回傳檔案路徑。失敗丟 ReviewFileError，訊息只含資料夾路徑與錯誤代碼。
 * @param {string} dir
 * @param {string} html
 * @returns {string}
 */
function writeReviewFile(dir, html) {
  const file = path.join(dir, REVIEW_FILE);
  const tmp = `${file}.${process.pid}.tmp`;
  try {
    fs.mkdirSync(dir, { recursive: true, mode: 0o700 });
    fs.writeFileSync(tmp, html, { encoding: "utf8", mode: 0o600 });
    fs.renameSync(tmp, file);
  } catch (err) {
    try {
      fs.rmSync(tmp, { force: true });
    } catch {
      // 暫存檔清不掉不影響結果
    }
    throw new ReviewFileError(`試跑清單檔寫不進 ${dir}（${err.code || err.name}），沒有送出任何寫入；請確認這個資料夾可以寫入，或用 WORKHUB_INBOX_STATE_DIR 換位置`);
  }
  return file;
}

module.exports = { REVIEW_FILE, ReviewFileError, buildReviewHtml, escapeHtml, writeReviewFile };
