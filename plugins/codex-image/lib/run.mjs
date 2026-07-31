/**
 * run.mjs — Codex CLI invocation for codex-image.
 * Prompt is passed via stdin (never in argv) to prevent shell injection.
 */
import { spawn } from 'node:child_process';

/**
 * Build the Codex CLI argument array. Pure function.
 * @param {{ stagingDir: string, extraArgs?: string[] }} opts
 * @returns {string[]}
 */
export function buildCodexArgs({ stagingDir, extraArgs = [] }) {
  return [
    'exec',
    '--json',
    '--ephemeral',
    '--skip-git-repo-check',
    '--sandbox', 'workspace-write',
    '-C', stagingDir,
    '-',
    ...extraArgs,
  ];
}

/**
 * Run Codex CLI with a prompt on stdin.
 * @param {{ codexBin?: string, prompt: string, stagingDir: string, timeoutMs?: number, spawnFn?: Function }} opts
 * @returns {Promise<{ exitCode: number|null, stdout: string, stderr: string, timedOut: boolean }>}
 */
export function runCodex({
  codexBin = 'codex',
  prompt,
  stagingDir,
  timeoutMs = 240000,
  spawnFn,
}) {
  const args = buildCodexArgs({ stagingDir });
  const doSpawn = spawnFn ?? spawn;

  return new Promise((resolve) => {
    const child = doSpawn(codexBin, args, {
      shell: false,
      stdio: ['pipe', 'pipe', 'pipe'],
    });

    let stdout = '';
    let stderr = '';
    let timedOut = false;
    let settled = false;

    const timer = setTimeout(() => {
      timedOut = true;
      child.kill('SIGTERM');
    }, timeoutMs);

    child.stdout.on('data', (chunk) => { stdout += chunk; });
    child.stderr.on('data', (chunk) => { stderr += chunk; });

    child.stdin.write(prompt);
    child.stdin.end();

    child.on('close', (code) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve({ exitCode: code, stdout, stderr, timedOut });
    });

    child.on('error', (err) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve({ exitCode: null, stdout, stderr: stderr + err.message, timedOut });
    });
  });
}
