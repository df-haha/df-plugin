/**
 * safe-path.mjs — Path sanitization and deny-list enforcement for codex-image.
 */
import fs from 'node:fs';
import path from 'node:path';

/**
 * Sanitize a filename to a safe basename. Appends .png if no extension.
 * Rejects anything containing path separators, traversal, NUL bytes, or control chars.
 * @param {string} name
 * @returns {string} sanitized basename
 * @throws {Error} on invalid input
 */
export function sanitizeFilename(name) {
  if (!name || typeof name !== 'string') {
    throw new Error('Filename must be a non-empty string');
  }

  // Reject NUL bytes and control characters (0x00-0x1F)
  if (/[\x00-\x1f]/.test(name)) {
    throw new Error('Filename must not contain NUL bytes or control characters');
  }

  // Reject path separators
  if (name.includes('/') || name.includes('\\')) {
    throw new Error('Filename must not contain path separators');
  }

  // Reject traversal
  if (name === '.' || name === '..' || name.includes('..')) {
    throw new Error('Filename must not contain path traversal');
  }

  // Reject absolute paths (path.basename would differ)
  if (path.basename(name) !== name) {
    throw new Error('Filename must be a plain basename, not a path');
  }

  // Append .png if no extension
  if (!path.extname(name)) {
    name = name + '.png';
  }

  return name;
}

/**
 * Resolve a directory that may not exist yet: realpath the nearest existing
 * ancestor, then re-append the components that do not exist yet.
 *
 * Re-appending is essential. Returning only the existing ancestor would
 * silently relocate the output (./a/b/c under a missing ./a becomes the cwd)
 * and, worse, would evaluate the deny list against a path the caller never
 * asked for.
 * @param {string} dir
 * @returns {string} absolute, realpath-resolved directory
 */
function resolveMaybeMissingDir(dir) {
  const resolved = path.resolve(dir);
  const missing = [];
  let current = resolved;

  while (true) {
    try {
      const real = fs.realpathSync.native(current);
      return missing.length ? path.join(real, ...missing.reverse()) : real;
    } catch {
      const parent = path.dirname(current);
      if (parent === current) {
        // Reached the filesystem root without finding anything that exists.
        return resolved;
      }
      missing.push(path.basename(current));
      current = parent;
    }
  }
}

/**
 * Resolve an output path, enforcing containment and deny-list.
 * @param {string} outputDir
 * @param {string} filename
 * @param {{ denyPaths?: string[] }} [opts]
 * @returns {string} absolute output path
 * @throws {Error} on containment violation or deny-list hit
 */
export function resolveOutputPath(outputDir, filename, opts = {}) {
  const denyPaths = opts.denyPaths ?? [];

  const realDir = resolveMaybeMissingDir(outputDir);

  // Build the full output path under the realpath-resolved directory, so a
  // symlinked output dir is compared at its real location, not its alias.
  const fullPath = path.join(realDir, filename);

  // Containment check: result must be inside realDir
  const relFromDir = path.relative(realDir, fullPath);
  if (relFromDir.startsWith('..') || path.isAbsolute(relFromDir)) {
    throw new Error(`Output path escapes the output directory: ${fullPath}`);
  }

  // Deny-list check: resolve each deny path and check containment
  for (const dp of denyPaths) {
    const resolvedDeny = path.resolve(dp);
    // Check if fullPath is inside the deny path (component-aware)
    const relFromDeny = path.relative(resolvedDeny, fullPath);
    if (!relFromDeny.startsWith('..') && !path.isAbsolute(relFromDeny)) {
      throw new Error(`Output path "${fullPath}" is inside denied path "${resolvedDeny}"`);
    }
  }

  return fullPath;
}

/**
 * Return a non-destructive path: if the file exists, append -v2, -v3, ... up to -v99.
 * @param {string} absPath
 * @returns {string} a path that does not currently exist
 * @throws {Error} if all versions up to v99 are taken
 */
export function nonDestructivePath(absPath) {
  if (!fs.existsSync(absPath)) return absPath;

  const dir = path.dirname(absPath);
  const ext = path.extname(absPath);
  const base = path.basename(absPath, ext);

  for (let i = 2; i <= 99; i++) {
    const candidate = path.join(dir, `${base}-v${i}${ext}`);
    if (!fs.existsSync(candidate)) return candidate;
  }
  throw new Error(`All versions v2-v99 exist for ${absPath}`);
}
