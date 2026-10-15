import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { configuredPythonPath } from './line-runtime.mjs';

const SCRIPT = fileURLToPath(new URL('./python/line-reader-status.py', import.meta.url));
const unavailable = () => ({ ok: false, code: 'LINE_CLIENT_STATUS_UNAVAILABLE',
  client: { verified: false }, process: { state: 'not_checked' } });
const closed = (value, fields) => value && typeof value === 'object' && !Array.isArray(value)
  && Object.keys(value).sort().join(',') === fields.slice().sort().join(',');

export function parseLineClientStatus(code, stdout) {
  try {
    if (typeof stdout !== 'string' || Buffer.byteLength(stdout) > 8192) return unavailable();
    const value = JSON.parse(stdout);
    if (code === 2 && closed(value, ['ok', 'code', 'client', 'process']) && value.ok === false
        && (value.code === 'LINE_BUILD_UNVERIFIED' || value.code === 'TRUSTED_BUILDS_INVALID')
        && closed(value.client, ['verified'])
        && value.client.verified === false && closed(value.process, ['state'])
        && value.process.state === 'not_checked') return value;
    if (code !== 0 || !closed(value, ['ok', 'client', 'process']) || value.ok !== true
        || !closed(value.client, ['verified', 'version', 'buildRef']) || value.client.verified !== true
        || !/^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)$/u.test(value.client.version)
        || !/^sha256:[0-9a-f]{64}$/u.test(value.client.buildRef)) return unavailable();
    const process = value.process;
    if (process?.state === 'running' && closed(process, ['state', 'processInstanceRef'])
        && /^process:[0-9a-f]{64}$/u.test(process.processInstanceRef)) return value;
    if (process?.state === 'ambiguous' && closed(process, ['state', 'processCount'])
        && Number.isSafeInteger(process.processCount) && process.processCount > 1
        && process.processCount < 2 ** 32) return value;
    if (closed(process, ['state']) && ['not_available', 'not_running', 'unverified'].includes(process.state)) return value;
  } catch {}
  return unavailable();
}

/** Process/build metadata only; no GUI, database, memory scan or chat scope. */
export function readLineClientStatus() {
  const pythonPath = configuredPythonPath();
  if (!pythonPath) return Promise.resolve(unavailable());
  return new Promise(resolve => {
    const child = spawn(pythonPath, ['-B', SCRIPT], { windowsHide: true, shell: false,
      stdio: ['ignore', 'pipe', 'pipe'] });
    const chunks = [];
    let settled = false, bytes = 0;
    const finish = result => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      child.kill();
      resolve(result);
    };
    const timer = setTimeout(() => finish(unavailable()), 10000);
    child.on('error', () => finish(unavailable()));
    child.stderr.on('data', () => {});
    child.stdout.on('data', chunk => {
      bytes += chunk.length;
      if (bytes > 8192) finish(unavailable());
      else chunks.push(chunk);
    });
    child.on('close', code => {
      finish(parseLineClientStatus(code, Buffer.concat(chunks).toString('utf8')));
    });
  });
}
