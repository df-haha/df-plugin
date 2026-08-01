#!/usr/bin/env node
/**
 * generate.mjs — CLI entry point for codex-image.
 *
 * The whole point of this file is that the caller never puts user text on a
 * command line. The job spec arrives as a single JSON document on stdin, and
 * the result leaves as a single JSON document on stdout, so an image
 * description containing $(), backticks, quotes, or newlines is inert data at
 * every hop.
 *
 * Usage:
 *   echo '<job-json>' | node lib/generate.mjs      # but prefer writing the
 *                                                  # job to a file and piping
 *                                                  # it, per repo shell rules
 *
 * Job spec:
 *   {
 *     "prompt":       "<full composed prompt>",   // required
 *     "outputDir":    "./assets/generated",       // required
 *     "filename":     "pear.png",                 // required
 *     "stagingDir":   "<abs path>",               // optional; a temp dir is made
 *     "denyPaths":    ["/mnt/"],                  // optional
 *     "timeoutMs":    240000,                     // optional
 *     "codexBin":     "codex",                    // optional
 *     "minBytesHint": 300000,                     // optional soft-signal floor
 *     "keepStaging":  false                       // optional; keep staging on success
 *   }
 *
 * A staging dir we created is removed on success and kept on failure (it holds
 * the evidence). A caller-supplied stagingDir is never removed.
 *
 * Result: { ok, checks, output, size, bytes, stagingDir, stagingKept, notes, stderrTail }
 * Exit code is 0 when every acceptance check passed, 1 otherwise.
 */
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

import { runCodex } from './run.mjs';
import { parseEvents, hasTurnCompleted, detectCodeDrawing, readPngSizeFromFile } from './parse-events.mjs';
import { sanitizeFilename, resolveOutputPath, nonDestructivePath } from './safe-path.mjs';

/** Soft signal only — the threshold drifts with size and quality. */
const DEFAULT_MIN_BYTES_HINT = 300 * 1000;

/**
 * Slack applied to the "was this file just created?" comparison.
 *
 * A filesystem can stamp a file slightly BEHIND Date.now() (measured at ~3ms on
 * ext4), and coarse filesystems — FAT and some network mounts, which matters on
 * the Windows target — round timestamps to 1-2 seconds. Comparing strictly
 * would discard genuinely new output and report a successful run as failed.
 */
const CREATION_TOLERANCE_MS = 2000;

/**
 * Read the whole of stdin as UTF-8.
 * @returns {Promise<string>}
 */
function readStdin() {
  return new Promise((resolve, reject) => {
    let data = '';
    process.stdin.setEncoding('utf8');
    process.stdin.on('data', (chunk) => { data += chunk; });
    process.stdin.on('end', () => resolve(data));
    process.stdin.on('error', reject);
  });
}

/**
 * List PNGs in a directory that were created at or after a cutoff.
 * Uses birthtime where available, falling back to mtime.
 * @param {string} dir
 * @param {number} cutoffMs
 * @returns {string[]} absolute paths, newest first
 */
export function newPngsSince(dir, cutoffMs) {
  let entries = [];
  try {
    entries = fs.readdirSync(dir, { withFileTypes: true });
  } catch {
    return [];
  }
  return entries
    .filter((entry) => entry.isFile() && /\.png$/i.test(entry.name))
    .map((entry) => {
      const absolutePath = path.join(dir, entry.name);
      const stats = fs.statSync(absolutePath);
      const created = stats.birthtimeMs || stats.mtimeMs;
      return { absolutePath, created };
    })
    .filter((item) => item.created >= cutoffMs)
    .sort((a, b) => b.created - a.created)
    .map((item) => item.absolutePath);
}

/**
 * Run the full generate → verify → copy flow for one image.
 * @param {object} job
 * @returns {Promise<object>} result document
 */
export async function generate(job) {
  if (!job || typeof job.prompt !== 'string' || job.prompt.trim() === '') {
    throw new Error('job.prompt is required');
  }
  if (typeof job.outputDir !== 'string' || job.outputDir === '') {
    throw new Error('job.outputDir is required');
  }

  const filename = sanitizeFilename(job.filename ?? 'image.png');
  const denyPaths = Array.isArray(job.denyPaths) ? job.denyPaths : [];
  const minBytesHint = Number(job.minBytesHint) || DEFAULT_MIN_BYTES_HINT;

  // Fail on a denied or escaping destination BEFORE spending any quota.
  const plannedPath = resolveOutputPath(job.outputDir, filename, { denyPaths });

  // Only a staging dir we created is ours to remove. A caller-supplied one is
  // left alone — deleting someone else's directory is never our call.
  const ownsStaging = !job.stagingDir;
  const stagingDir = job.stagingDir
    ?? fs.mkdtempSync(path.join(os.tmpdir(), 'codex-image-'));
  fs.mkdirSync(stagingDir, { recursive: true });

  // Snapshot what was already there: anything present before the run is not
  // ours, regardless of what its timestamp says.
  const preExisting = new Set(newPngsSince(stagingDir, 0));
  const startedAt = Date.now() - CREATION_TOLERANCE_MS;
  const run = await runCodex({
    codexBin: job.codexBin ?? 'codex',
    prompt: job.prompt,
    stagingDir,
    timeoutMs: Number(job.timeoutMs) || 240000,
    spawnFn: job.spawnFn,
  });

  const { events } = parseEvents(run.stdout);
  // The timestamp tolerance could re-admit a pre-existing file, so exclude the
  // snapshot explicitly. Both conditions must hold: recent AND not there before.
  const produced = newPngsSince(stagingDir, startedAt).filter((p) => !preExisting.has(p));
  const primary = produced[0] ?? null;
  const size = primary ? readPngSizeFromFile(primary) : null;
  const bytes = primary ? fs.statSync(primary).size : null;
  const drawing = detectCodeDrawing(events);

  const checks = {
    exitCodeZero: run.exitCode === 0 && !run.timedOut,
    turnCompleted: hasTurnCompleted(events),
    newPngCreated: primary !== null,
    ihdrReadable: size !== null,
    // Soft signal, never on its own a failure — reported, not enforced.
    fileSizePlausible: bytes === null ? null : bytes >= minBytesHint,
    noCodeDrawing: !drawing.suspected,
  };

  const notes = [];
  if (checks.fileSizePlausible === false) {
    notes.push(
      `檔案僅 ${bytes} bytes，低於 ${minBytesHint} 的軟性參考值；這是啟發式訊號（閾值隨 size / quality 漂移），不是失敗判準。`,
    );
  }
  if (drawing.suspected) {
    notes.push(`偵測到疑似 code-drawing（啟發式）：${drawing.evidence.join('; ')}`);
  }
  if (run.timedOut) notes.push('Codex CLI 呼叫逾時。');

  // The file-size heuristic is deliberately excluded from the pass/fail gate.
  const hardChecks = ['exitCodeZero', 'turnCompleted', 'newPngCreated', 'ihdrReadable', 'noCodeDrawing'];
  const ok = hardChecks.every((key) => checks[key] === true);

  let output = null;
  if (ok) {
    fs.mkdirSync(path.dirname(plannedPath), { recursive: true });
    output = nonDestructivePath(plannedPath);
    fs.copyFileSync(primary, output);
  }

  // Clean up only on success. On failure the staging dir is the evidence —
  // the PNG that was produced, or the absence of one — so it is kept for
  // inspection and its path is reported back.
  let stagingKept = true;
  if (ok && ownsStaging && !job.keepStaging) {
    fs.rmSync(stagingDir, { recursive: true, force: true });
    stagingKept = false;
  }

  return {
    ok,
    checks,
    heuristics: { codeDrawing: drawing },
    output,
    stagingDir,
    stagingKept,
    size,
    bytes,
    notes,
    exitCode: run.exitCode,
    timedOut: run.timedOut,
    stderrTail: run.stderr.split('\n').slice(-20).join('\n'),
  };
}

/* c8 ignore start — process wiring, exercised end-to-end rather than in unit tests */
const invokedDirectly = process.argv[1]
  && path.resolve(process.argv[1]) === path.resolve(new URL(import.meta.url).pathname);

if (invokedDirectly) {
  readStdin()
    .then((raw) => generate(JSON.parse(raw)))
    .then((result) => {
      process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
      process.exit(result.ok ? 0 : 1);
    })
    .catch((err) => {
      process.stdout.write(`${JSON.stringify({ ok: false, error: err.message }, null, 2)}\n`);
      process.exit(1);
    });
}
/* c8 ignore stop */
