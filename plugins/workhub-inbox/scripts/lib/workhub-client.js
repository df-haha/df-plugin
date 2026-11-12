"use strict";

// Work Hub MCP 端點的最小客戶端：stateless HTTP JSON-RPC `tools/call`，不依賴 MCP SDK。
// 只對 429／502／503／504 與網路錯誤重試；寫入工具一律帶 idempotency_key，重試不會重複寫入。
// 金鑰保護：送出任何請求前先驗證端點；不跟隨轉址；錯誤訊息只帶錯誤代碼，不帶底層訊息或端點原字串。
// 伺服器回的錯誤文字一律不輸出（可能回印送出的標題等第三方內容）：只取結構化代碼，取不到就用固定字串。

const RETRYABLE_STATUS = new Set([429, 502, 503, 504]);
const NETWORK_ERROR = /ECONNRESET|ETIMEDOUT|ECONNREFUSED|socket hang up|fetch failed|network/i;
const DEFAULT_TIMEOUT_MS = 30_000;
const LOOPBACK_HOSTS = new Set(["127.0.0.1", "localhost", "[::1]"]);
const API_KEY_FORMAT = /^[\x21-\x7e]+$/;
const ERROR_CODE_FORMAT = /^[A-Za-z0-9_]{1,40}$/;

// 金鑰變數依序取第一個有值（非空字串）的：WORKHUB_INBOX_API_KEY 優先；
// 沒設就用 PM 系統「AI 串接」頁教使用者設的 DF_WORKHUB_API_KEY（一人只有一把有效金鑰，不必設兩次）。
// 前面的變數有值但格式錯，照樣用它並報錯，不悄悄改用後面那把。端點不設後備，只認 WORKHUB_INBOX_ENDPOINT。
const API_KEY_VARS = ["WORKHUB_INBOX_API_KEY", "DF_WORKHUB_API_KEY"];

const ENDPOINT_INVALID = "端點網址格式不正確：請確認 WORKHUB_INBOX_ENDPOINT 是 https 開頭的網址（例如 https://<Work Hub 網址>/mcp），且沒有把金鑰填到這個變數。";
const API_KEY_MISSING = `缺少 Work Hub API 金鑰（${API_KEY_VARS.join(" 或 ")}）`;
const apiKeyInvalid = (name) => `API 金鑰格式不正確：${name} 不能有空白、換行或中文，請重新複製金鑰後再設定一次。`;
const SERVER_CODE_FORMAT = /^[A-Z][A-Z0-9_]{1,63}$/;
const SERVER_TEXT_HIDDEN = "SERVER_ERROR_TEXT_HIDDEN";
// Work Hub 工具錯誤是 HTTP 200＋structuredContent.error，沒有狀態碼；這兩個代碼代表金鑰本身無效或權限不足
// （marketing-pm-system src/server/mcp/errors.js 的 McpAuthenticationError／McpAuthorizationError）。
const AUTH_CODE_STATUS = { MCP_UNAUTHENTICATED: 401, MCP_FORBIDDEN: 403 };

class WorkhubError extends Error {
  constructor(message, { status = null, tool = null, retryable = false } = {}) {
    super(message);
    this.name = "WorkhubError";
    this.status = status;
    this.tool = tool;
    this.retryable = retryable;
  }
}

/**
 * 驗證端點，回傳正規化後的網址。只接受 https；http 只放行本機（127.0.0.1、localhost、[::1]）；
 * 網址不得含帳號密碼。失敗一律丟固定文字，不回印原字串（使用者可能把金鑰填進這個變數）。
 * @param {string} raw
 * @returns {string}
 */
function validateEndpoint(raw) {
  let url;
  try {
    url = new URL(String(raw ?? ""));
  } catch {
    throw new WorkhubError(ENDPOINT_INVALID);
  }
  if (url.username || url.password) throw new WorkhubError(ENDPOINT_INVALID);
  if (url.protocol === "https:") return url.href;
  if (url.protocol === "http:" && LOOPBACK_HOSTS.has(url.hostname)) return url.href;
  throw new WorkhubError(ENDPOINT_INVALID);
}

/** 底層錯誤 → 只取錯誤代碼或類別名稱（例 ECONNREFUSED、TimeoutError），不取 message。 */
function errorCode(err) {
  for (const candidate of [err && err.cause && err.cause.code, err && err.code, err && err.name]) {
    if (typeof candidate === "string" && ERROR_CODE_FORMAT.test(candidate)) return candidate;
  }
  return "未知錯誤";
}

function isNetworkError(err, code) {
  const causeMessage = err && err.cause && err.cause.message;
  return NETWORK_ERROR.test(String(err && err.message)) || NETWORK_ERROR.test(String(causeMessage)) || NETWORK_ERROR.test(code) || code === "TimeoutError";
}

/** 結構化錯誤代碼：只接受大寫英數底線（例 VALIDATION_ERROR），其他一律當沒有。 */
function serverCode(value) {
  return typeof value === "string" && SERVER_CODE_FORMAT.test(value) ? value : null;
}

function parseResult(result, tool) {
  if (result.isError) {
    const error = result.structuredContent && result.structuredContent.error;
    const code = serverCode(error && error.code);
    throw new WorkhubError(`${tool} 失敗（${code || SERVER_TEXT_HIDDEN}）`, { tool, status: (code && AUTH_CODE_STATUS[code]) || null });
  }
  if (result.structuredContent !== undefined) return result.structuredContent;
  const text = (result.content || []).find((c) => c.type === "text")?.text;
  if (text === undefined) return null;
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

/**
 * 從環境變數取金鑰。回傳實際使用的金鑰與變數名，以及所有金鑰變數的原值（給輸出遮蔽用，有值的都要遮）。
 * @param {Record<string, string|undefined>} env
 * @returns {{apiKey: string|undefined, apiKeyVar: string|null, secrets: Array<string|undefined>}}
 */
function resolveApiKey(env) {
  const apiKeyVar = API_KEY_VARS.find((name) => typeof env[name] === "string" && env[name].length > 0) || null;
  return { apiKey: apiKeyVar ? env[apiKeyVar] : undefined, apiKeyVar, secrets: API_KEY_VARS.map((name) => env[name]) };
}

function createClient({ endpoint, apiKey, apiKeyVar = API_KEY_VARS[0], fetchImpl = fetch, sleep = (ms) => new Promise((r) => setTimeout(r, ms)), maxAttempts = 3, timeoutMs = DEFAULT_TIMEOUT_MS }) {
  if (!endpoint) throw new WorkhubError("缺少 Work Hub MCP 端點（WORKHUB_INBOX_ENDPOINT）");
  if (!apiKey) throw new WorkhubError(API_KEY_MISSING);
  const target = validateEndpoint(endpoint);
  // 變數名只用固定清單裡的，錯誤訊息不帶呼叫端給的任意字串
  if (!API_KEY_FORMAT.test(apiKey)) throw new WorkhubError(apiKeyInvalid(API_KEY_VARS.includes(apiKeyVar) ? apiKeyVar : API_KEY_VARS[0]));
  let nextId = 1;

  async function once(tool, args) {
    let res;
    try {
      res = await fetchImpl(target, {
        method: "POST",
        headers: { "content-type": "application/json", accept: "application/json, text/event-stream", authorization: `Bearer ${apiKey}` },
        body: JSON.stringify({ jsonrpc: "2.0", id: nextId++, method: "tools/call", params: { name: tool, arguments: args } }),
        // 不跟隨轉址：轉址目標沒經過端點驗證，請求內容（含寫入資料）不該被帶過去
        redirect: "manual",
        signal: AbortSignal.timeout(timeoutMs),
      });
    } catch (err) {
      const code = errorCode(err);
      throw new WorkhubError(`${tool} 連線失敗（${code}）`, { tool, retryable: isNetworkError(err, code) });
    }
    if (res.status >= 300 && res.status < 400) {
      throw new WorkhubError(`${tool} 端點回了轉址（HTTP ${res.status}），為避免金鑰與資料被帶到別的網址已停止；請確認 WORKHUB_INBOX_ENDPOINT 是完整的 Work Hub MCP 網址`, { status: res.status, tool });
    }
    if (!res.ok) {
      throw new WorkhubError(`${tool} HTTP ${res.status}`, { status: res.status, tool, retryable: RETRYABLE_STATUS.has(res.status) });
    }
    let raw;
    try {
      raw = await res.text();
    } catch (err) {
      // 伺服器可能已處理完；寫入都帶 idempotency_key，重送安全
      throw new WorkhubError(`${tool} 讀取回應中斷（${errorCode(err)}）`, { tool, retryable: true });
    }
    const dataLine = raw.split(/\r?\n/).find((l) => l.startsWith("data:"));
    let body;
    try {
      body = JSON.parse(dataLine ? dataLine.slice(5) : raw);
    } catch {
      throw new WorkhubError(`${tool} 回應不是 JSON`, { tool });
    }
    if (!body || typeof body !== "object") throw new WorkhubError(`${tool} 回應格式不符`, { tool });
    if (body.error) {
      const code = Number.isInteger(body.error.code) ? `JSON-RPC ${body.error.code}` : SERVER_TEXT_HIDDEN;
      throw new WorkhubError(`${tool} 失敗（${code}）`, { tool });
    }
    return parseResult(body.result || {}, tool);
  }

  async function callTool(tool, args) {
    for (let attempt = 1; ; attempt += 1) {
      try {
        return await once(tool, args);
      } catch (err) {
        if (!(err instanceof WorkhubError) || !err.retryable || attempt >= maxAttempts) throw err;
        await sleep(500 * 2 ** (attempt - 1));
      }
    }
  }

  return { callTool };
}

module.exports = { WorkhubError, createClient, resolveApiKey, validateEndpoint };
