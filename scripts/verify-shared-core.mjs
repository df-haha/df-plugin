#!/usr/bin/env node

// verify-shared-core.mjs — Generator / validator for shared verification-core blocks.
//
// Reads canonical sections from shared/verification-core.md and syncs them
// into consumer plugin files via BEGIN/END markers.
//
// Marker protocol:
//   <!-- BEGIN SHARED:verification-core:<id> v<ver> sha:<12-char-hex> (generated; ...) -->
//   ...content...
//   <!-- END SHARED:verification-core:<id> -->
//
// Usage:
//   node scripts/verify-shared-core.mjs --check   (default) verify all blocks match canonical
//   node scripts/verify-shared-core.mjs --write   overwrite blocks with canonical content
//   node scripts/verify-shared-core.mjs --help    show usage
//
// Adding a new consumer:
//   1. Add an entry to the CONSUMERS array below
//   2. Place empty BEGIN/END markers at the desired position in the consumer file
//   3. Run --write to populate the blocks

import { readFileSync, writeFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(__dirname, "..");

const CANONICAL_PATH = "shared/verification-core.md";

const CONSUMERS = [
  {
    file: "plugins/deep-research-ryan/skills/deep-research-ryan/references/verification.md",
    sections: ["source-class", "independence", "access-state"],
  },
  {
    file: "plugins/deep-research-ryan/skills/deep-research-ryan/references/quality-gate.md",
    sections: ["data-consistency"],
  },
  {
    file: "plugins/fact-check/skills/fact-check/references/verification-details.md",
    sections: ["source-class", "independence", "access-state", "data-consistency"],
  },
];

function normalize(text) {
  return text
    .replace(/\r\n/g, "\n")
    .replace(/[ \t]+$/gm, "")
    .replace(/^\n+/, "")
    .replace(/\n+$/, "");
}

function sha256Short(text) {
  return createHash("sha256").update(text, "utf8").digest("hex").slice(0, 12);
}

function parseCanonical(filePath) {
  const raw = readFileSync(filePath, "utf8");

  const versionMatch = raw.match(/^core_version:\s*(\d+)/m);
  if (!versionMatch) {
    throw new Error(`${CANONICAL_PATH}: missing core_version field`);
  }
  const coreVersion = parseInt(versionMatch[1], 10);

  const sections = new Map();
  const sectionRe = /<!-- SECTION:(\S+) -->\n([\s\S]*?)<!-- \/SECTION:\1 -->/g;
  let m;
  while ((m = sectionRe.exec(raw)) !== null) {
    sections.set(m[1], normalize(m[2]));
  }

  return { coreVersion, sections };
}

function beginMarker(id, ver, sha) {
  return `<!-- BEGIN SHARED:verification-core:${id} v${ver} sha:${sha} (generated；改 shared/verification-core.md 後跑 node scripts/verify-shared-core.mjs --write，禁止手改本區塊) -->`;
}

function endMarker(id) {
  return `<!-- END SHARED:verification-core:${id} -->`;
}

function beginRe(id) {
  return new RegExp(
    `<!-- BEGIN SHARED:verification-core:${id} v\\d+ sha:[0-9a-f]{12} \\(generated[^)]*\\) -->`
  );
}

function processFile(filePath, sectionIds, canonical, mode) {
  const abs = resolve(ROOT, filePath);
  let content = readFileSync(abs, "utf8");
  const errors = [];

  for (const id of sectionIds) {
    const canonicalContent = canonical.sections.get(id);
    if (!canonicalContent) {
      errors.push({ file: filePath, section: id, error: `section "${id}" not found in ${CANONICAL_PATH}` });
      continue;
    }

    const canonicalSha = sha256Short(canonicalContent);
    const begin = beginRe(id);
    const end = endMarker(id);

    const beginMatch = content.match(begin);
    if (!beginMatch) {
      errors.push({ file: filePath, section: id, error: "BEGIN marker not found" });
      continue;
    }

    const endIdx = content.indexOf(end, beginMatch.index);
    if (endIdx === -1) {
      errors.push({ file: filePath, section: id, error: "END marker not found" });
      continue;
    }

    const blockStart = beginMatch.index + beginMatch[0].length;
    const existingBlock = content.slice(blockStart, endIdx);
    const existingNormalized = normalize(existingBlock);

    if (mode === "check") {
      if (existingNormalized !== canonicalContent) {
        errors.push({
          file: filePath,
          section: id,
          error: `content mismatch (canonical sha:${canonicalSha}, block sha:${sha256Short(existingNormalized)})`,
        });
      } else {
        const markerShaMatch = beginMatch[0].match(/sha:([0-9a-f]{12})/);
        if (markerShaMatch && markerShaMatch[1] !== canonicalSha) {
          errors.push({
            file: filePath,
            section: id,
            error: `marker sha (${markerShaMatch[1]}) does not match content sha (${canonicalSha})`,
          });
        }
      }
    } else if (mode === "write") {
      const newBegin = beginMarker(id, canonical.coreVersion, canonicalSha);
      const newBlock = `${newBegin}\n${canonicalContent}\n${end}`;
      const oldBlock = content.slice(beginMatch.index, endIdx + end.length);
      content = content.replace(oldBlock, newBlock);
    }
  }

  if (mode === "write" && errors.length === 0) {
    writeFileSync(abs, content, "utf8");
  }

  return errors;
}

function main() {
  const args = process.argv.slice(2);

  if (args.includes("--help")) {
    console.log(`verify-shared-core.mjs — Sync shared verification-core blocks

Usage:
  node scripts/verify-shared-core.mjs [--check|--write|--help]

Modes:
  --check  (default)  Verify all consumer blocks match canonical content
  --write             Overwrite consumer blocks with canonical content
  --help              Show this help

Consumers:
${CONSUMERS.map((c) => `  ${c.file}\n    sections: ${c.sections.join(", ")}`).join("\n")}
`);
    process.exit(0);
  }

  const mode = args.includes("--write") ? "write" : "check";

  const canonicalFile = resolve(ROOT, CANONICAL_PATH);
  let canonical;
  try {
    canonical = parseCanonical(canonicalFile);
  } catch (e) {
    console.error(`Error reading canonical file: ${e.message}`);
    process.exit(1);
  }

  console.log(`Mode: ${mode}`);
  console.log(`Canonical: ${CANONICAL_PATH} (core_version: ${canonical.coreVersion}, ${canonical.sections.size} sections)\n`);

  let allErrors = [];

  for (const consumer of CONSUMERS) {
    const errors = processFile(consumer.file, consumer.sections, canonical, mode);
    allErrors = allErrors.concat(errors);

    if (mode === "write" && errors.length === 0) {
      console.log(`  WROTE ${consumer.file} (${consumer.sections.length} sections)`);
    } else if (mode === "check" && errors.length === 0) {
      console.log(`  OK    ${consumer.file}`);
    }

    for (const err of errors) {
      console.error(`  FAIL  ${err.file} [${err.section}]: ${err.error}`);
    }
  }

  console.log("");
  if (allErrors.length > 0) {
    console.error(`${allErrors.length} error(s) found.`);
    process.exit(1);
  } else {
    console.log(`All blocks ${mode === "write" ? "written" : "verified"} successfully.`);
    process.exit(0);
  }
}

main();
