/**
 * env.mjs — Cross-platform detection for codex-image plugin.
 * Windows is a primary target; no bash or /proc assumptions beyond guarded reads.
 */
import { readFileSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { execFileSync } from 'node:child_process';

/**
 * Detect the current platform.
 * @param {{ platform?: string, env?: Record<string,string|undefined> }} [overrides]
 * @returns {'win32' | 'wsl' | 'linux' | 'darwin'}
 */
export function detectPlatform(overrides = {}) {
  const plat = overrides.platform ?? process.platform;
  if (plat === 'win32') return 'win32';
  if (plat === 'darwin') return 'darwin';
  if (plat === 'linux') {
    const env = overrides.env ?? process.env;
    if (env.WSL_DISTRO_NAME) return 'wsl';
    try {
      const procVersion = readFileSync('/proc/version', 'utf8');
      if (/microsoft/i.test(procVersion)) return 'wsl';
    } catch {
      // /proc/version not readable — not WSL
    }
    return 'linux';
  }
  return 'linux';
}

/**
 * Resolve the Codex home directory.
 * @param {{ env?: Record<string,string|undefined> }} [overrides]
 * @returns {string}
 */
export function resolveCodexHome(overrides = {}) {
  const env = overrides.env ?? process.env;
  if (env.CODEX_HOME && env.CODEX_HOME.length > 0) return env.CODEX_HOME;
  return path.join(os.homedir(), '.codex');
}

/**
 * Resolve the config file path for codex-image.
 * @param {{ env?: Record<string,string|undefined> }} [overrides]
 * @returns {string}
 */
export function resolveConfigPath(overrides = {}) {
  return path.join(resolveCodexHome(overrides), 'codex-image.local.md');
}

/**
 * Default spawn-based probe for Python detection.
 * @param {string} cmd
 * @param {string[]} args
 * @returns {boolean}
 */
function defaultProbe(cmd, args) {
  try {
    execFileSync(cmd, args, { timeout: 5000, stdio: 'pipe' });
    return true;
  } catch {
    return false;
  }
}

/**
 * Detect available Python command.
 * @param {{ probe?: (cmd: string, args: string[]) => boolean }} [deps]
 * @returns {{ cmd: string, args: string[] } | null}
 */
export function detectPythonCommand(deps = {}) {
  const probe = deps.probe ?? defaultProbe;
  const candidates = [
    { cmd: 'python3', args: [] },
    { cmd: 'python', args: [] },
    { cmd: 'py', args: ['-3'] },
  ];
  for (const { cmd, args } of candidates) {
    if (probe(cmd, [...args, '--version'])) {
      return { cmd, args };
    }
  }
  return null;
}

/**
 * Return default deny-write paths for the given platform.
 * @param {'win32' | 'wsl' | 'linux' | 'darwin'} platform
 * @returns {string[]}
 */
export function defaultDenyWritePaths(platform) {
  if (platform === 'wsl') return ['/mnt/'];
  return [];
}
