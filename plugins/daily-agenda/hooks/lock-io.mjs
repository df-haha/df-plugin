/**
 * Shared I/O helpers for the taint-lock system (used by both PreToolUse
 * lock.mjs and PostToolUse post-lock.mjs).
 *
 * Keeps marker operations, home resolution, and config reading in one place
 * so the two hooks cannot drift on M2 transcript_path logic or cleanup rules.
 */

import { readFileSync, writeFileSync, mkdirSync, existsSync, statSync,
         readdirSync, unlinkSync, renameSync } from 'node:fs';
import { randomBytes } from 'node:crypto';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { markerPath, draftKeys } from './lock-core.mjs';

// ---------------------------------------------------------------------------
// Raw sentinel — loaded from raw-commands.json (single source of truth
// shared with Python fence.RAW_SENTINEL).
// ---------------------------------------------------------------------------

let _sentinelCache = null;

export const loadSentinel = () => {
  if (_sentinelCache) return _sentinelCache;
  try {
    const data = JSON.parse(readFileSync(
      join(dirname(fileURLToPath(import.meta.url)), 'raw-commands.json'),
      'utf8',
    ));
    _sentinelCache = data.raw_sentinel || 'DA-RAW-3f9a';
  } catch {
    _sentinelCache = 'DA-RAW-3f9a';
  }
  return _sentinelCache;
};

// ---------------------------------------------------------------------------
// Resolve daily-agenda home directory
// ---------------------------------------------------------------------------

export const resolveHome = () => {
  const envHome = process.env.DAILY_AGENDA_HOME;
  if (envHome) return envHome;
  const userProfile = process.env.USERPROFILE;
  if (userProfile) return join(userProfile, '.daily-agenda');
  const home = process.env.HOME;
  if (home) return join(home, '.daily-agenda');
  return null;
};

// ---------------------------------------------------------------------------
// Taint marker operations
// ---------------------------------------------------------------------------

export const isSessionTainted = (daHome, sanitizedId) => {
  try {
    return existsSync(markerPath(daHome, sanitizedId));
  } catch {
    return false;
  }
};

export const markSessionTainted = (daHome, sanitizedId, reason, transcriptPath) => {
  try {
    mkdirSync(join(daHome, 'taint'), { recursive: true });
    const marker = {
      tainted_at: new Date().toISOString(),
      reason,
    };
    if (typeof transcriptPath === 'string' && transcriptPath.length > 0) {
      marker.transcript_path = transcriptPath;
    }
    writeFileSync(markerPath(daHome, sanitizedId), JSON.stringify(marker), 'utf8');
    return true;
  } catch {
    return false;
  }
};

/**
 * Opportunistic cleanup of markers older than 60 days.
 *
 * M2 safe-delete rule: a marker is only deleted if it is older than 60 days
 * AND its recorded transcript file no longer exists.  Legacy markers (no
 * transcript_path) are kept (fail closed).
 */
export const cleanupOldMarkers = (daHome) => {
  try {
    const taintDir = join(daHome, 'taint');
    const files = readdirSync(taintDir);
    const now = Date.now();
    const maxAge = 60 * 24 * 60 * 60 * 1000;
    for (const f of files) {
      if (f === 'tainted-drafts.json') continue; // not a session marker
      try {
        const fp = join(taintDir, f);
        const st = statSync(fp);
        if (now - st.mtimeMs <= maxAge) continue;

        let markerData;
        try {
          markerData = JSON.parse(readFileSync(fp, 'utf8'));
        } catch {
          continue; // Unparsable → keep
        }

        const tp = markerData?.transcript_path;
        if (typeof tp !== 'string' || tp.length === 0) continue; // Legacy → keep
        if (existsSync(tp)) continue; // Transcript exists → keep

        unlinkSync(fp);
      } catch { /* ignore per-file errors */ }
    }
  } catch { /* ignore */ }
};

// ---------------------------------------------------------------------------
// Read extra taint prefixes from config
// ---------------------------------------------------------------------------

export const readExtraTaintPrefixes = (daHome) => {
  try {
    const raw = readFileSync(join(daHome, 'config.json'), 'utf8');
    const cfg = JSON.parse(raw);
    const outputDir = cfg?.output?.dir;
    if (typeof outputDir === 'string' && outputDir.length > 0) return [outputDir];
  } catch { /* fail-open */ }
  return [];
};

// ---------------------------------------------------------------------------
// Tainted-drafts list (draft laundering prevention)
// ---------------------------------------------------------------------------

const DRAFTS_LIST_PATH = 'taint/tainted-drafts.json';

/**
 * Load the set of canonicalized draft paths written during tainted sessions.
 */
export const loadTaintedDrafts = (daHome) => {
  try {
    const raw = readFileSync(join(daHome, DRAFTS_LIST_PATH), 'utf8');
    const arr = JSON.parse(raw);
    return new Set(Array.isArray(arr) ? arr : []);
  } catch {
    return new Set();
  }
};

/**
 * Record a draft path written during a tainted session.
 * Atomic: re-reads + merges + writes to unique tmp then renames.
 * Returns true on success, false on failure.
 */
export const appendTaintedDraft = (daHome, canonicalPath) => {
  try {
    // Re-read right before write to handle concurrent sessions
    const existing = loadTaintedDrafts(daHome);
    for (const k of draftKeys(canonicalPath)) existing.add(k);
    mkdirSync(join(daHome, 'taint'), { recursive: true });
    const listPath = join(daHome, DRAFTS_LIST_PATH);
    const suffix = `${process.pid}.${randomBytes(4).toString('hex')}`;
    const tmpPath = `${listPath}.${suffix}.tmp`;
    writeFileSync(tmpPath, JSON.stringify([...existing]), 'utf8');
    renameSync(tmpPath, listPath);
    return true;
  } catch {
    return false;
  }
};
