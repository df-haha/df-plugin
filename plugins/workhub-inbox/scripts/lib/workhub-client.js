"use strict";

// Work Hub MCP 端點的最小客戶端：stateless HTTP JSON-RPC `tools/call`，不依賴 MCP SDK。
// 只對 429／502／503／504 與網路錯誤重試；寫入工具一律帶 idempotency_key，重試不會重複寫入。

const RETRYABLE_STATUS = new Set([429, 502, 503, 504]);
const NETWORK_ERROR = /ECONNRESET|ETIMEDOUT|ECONNREFUSED|socket hang up|fetch failed|network/i;
const DEFAULT_TIMEOUT_MS = 30_000;

class WorkhubError extends Error {
  constructor(message, { status = null, tool = null, retryable = false } = {}) {
    super(message);
    this.name = "WorkhubError";
    this.status = status;
    this.tool = tool;
    this.retryable = retryable;
  }
}

function parseResult(result, tool) {
  if (result.isError) {
    const text = (result.content || []).map((c) => c.text || "").join(" ").trim();
    throw new WorkhubError(`${tool} 失敗：${text.slice(0, 300) || "未知錯誤"}`, { tool });
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

function createClient({ endpoint, apiKey, fetchImpl = fetch, sleep = (ms) => new Promise((r) => setTimeout(r, ms)), maxAttempts = 3, timeoutMs = DEFAULT_TIMEOUT_MS }) {
  if (!endpoint) throw new WorkhubError("缺少 Work Hub MCP 端點（--endpoint 或 WORKHUB_INBOX_ENDPOINT）");
  if (!apiKey) throw new WorkhubError("缺少 Work Hub API 金鑰（WORKHUB_INBOX_API_KEY）");
  let nextId = 1;

  async function once(tool, args) {
    let res;
    try {
      res = await fetchImpl(endpoint, {
        method: "POST",
        headers: { "content-type": "application/json", accept: "application/json, text/event-stream", authorization: `Bearer ${apiKey}` },
        body: JSON.stringify({ jsonrpc: "2.0", id: nextId++, method: "tools/call", params: { name: tool, arguments: args } }),
        signal: AbortSignal.timeout(timeoutMs),
      });
    } catch (err) {
      throw new WorkhubError(`${tool} 連線失敗：${err.message}`, { tool, retryable: NETWORK_ERROR.test(String(err.message)) || err.name === "TimeoutError" });
    }
    if (!res.ok) {
      throw new WorkhubError(`${tool} HTTP ${res.status}`, { status: res.status, tool, retryable: RETRYABLE_STATUS.has(res.status) });
    }
    const raw = await res.text();
    const dataLine = raw.split(/\r?\n/).find((l) => l.startsWith("data:"));
    let body;
    try {
      body = JSON.parse(dataLine ? dataLine.slice(5) : raw);
    } catch {
      throw new WorkhubError(`${tool} 回應不是 JSON`, { tool });
    }
    if (body.error) throw new WorkhubError(`${tool} 失敗：${body.error.message || body.error.code}`, { tool });
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

module.exports = { WorkhubError, createClient };
