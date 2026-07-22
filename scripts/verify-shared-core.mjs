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

const USAGE = `verify-shared-core.mjs — Sync shared verification-core blocks

Usage:
  node scripts/verify-shared-core.mjs [--check|--write|--help]

Modes:
  --check  (default)  Verify all consumer blocks match canonical content
  --write             Overwrite consumer blocks with canonical content
  --help              Show this help

Consumers:
${CONSUMERS.map((c) => `  ${c.file}\n    sections: ${c.sections.join(", ")}`).join("\n")}
`;

// 讀檔後一律正規化 CRLF→LF，確保跨平台行為一致
function readNormalized(filePath) {
  return readFileSync(filePath, "utf8").replace(/\r\n/g, "\n");
}

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
  const raw = readNormalized(filePath);

  const versionMatch = raw.match(/^core_version:\s*(\d+)/m);
  if (!versionMatch) {
    throw new Error(`${CANONICAL_PATH}: missing core_version field`);
  }
  const coreVersion = parseInt(versionMatch[1], 10);

  // 先獨立計數 open/close marker——non-greedy 配對會把巢狀的第二個 open 吞進內容，
  // 所以重複/巢狀偵測不能依賴配對結果，必須先全域數 marker 出現次數
  const openCounts = new Map();
  const closeCounts = new Map();
  for (const mm of raw.matchAll(/<!-- SECTION:(\S+) -->/g)) {
    openCounts.set(mm[1], (openCounts.get(mm[1]) || 0) + 1);
  }
  for (const mm of raw.matchAll(/<!-- \/SECTION:(\S+) -->/g)) {
    closeCounts.set(mm[1], (closeCounts.get(mm[1]) || 0) + 1);
  }
  for (const id of new Set([...openCounts.keys(), ...closeCounts.keys()])) {
    const o = openCounts.get(id) || 0;
    const c = closeCounts.get(id) || 0;
    if (o !== 1 || c !== 1) {
      throw new Error(
        `${CANONICAL_PATH}: SECTION id "${id}" has ${o} open / ${c} close marker(s) — each id must appear exactly once (duplicate or nested markers?)`
      );
    }
  }

  const sections = new Map();
  const sectionRe = /<!-- SECTION:(\S+) -->\n([\s\S]*?)<!-- \/SECTION:\1 -->/g;
  let m;
  while ((m = sectionRe.exec(raw)) !== null) {
    const body = m[2];
    // 內容中不得再出現任何 SECTION marker（防不同 id 巢狀）
    if (/<!-- \/?SECTION:/.test(body)) {
      throw new Error(`${CANONICAL_PATH}: SECTION "${m[1]}" contains a nested SECTION marker`);
    }
    sections.set(m[1], normalize(body));
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

// 驗證 consumer 檔案的 marker 完整性：每個 section 的 BEGIN/END 各只能出現一次
function validateMarkerCounts(content, sectionIds, filePath) {
  const errors = [];
  for (const id of sectionIds) {
    const beginPattern = new RegExp(
      `<!-- BEGIN SHARED:verification-core:${id} `, "g"
    );
    const endPattern = new RegExp(
      `<!-- END SHARED:verification-core:${id} -->`, "g"
    );
    const beginCount = (content.match(beginPattern) || []).length;
    const endCount = (content.match(endPattern) || []).length;
    if (beginCount !== 1) {
      errors.push({ file: filePath, section: id, error: `expected exactly 1 BEGIN marker, found ${beginCount}` });
    }
    if (endCount !== 1) {
      errors.push({ file: filePath, section: id, error: `expected exactly 1 END marker, found ${endCount}` });
    }
  }
  return errors;
}

function processFile(filePath, sectionIds, canonical, mode) {
  const abs = resolve(ROOT, filePath);
  let content;
  try {
    content = readNormalized(abs);
  } catch (e) {
    return [{ file: filePath, section: "*", error: `cannot read file: ${e.message}` }];
  }

  // 先檢查重複/巢狀 marker
  const markerErrors = validateMarkerCounts(content, sectionIds, filePath);
  if (markerErrors.length > 0) {
    return markerErrors;
  }

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

    // block 內不得再出現任何 SHARED marker（防跨 id 巢狀導致 --write 截斷錯位）
    if (existingBlock.includes("SHARED:verification-core:")) {
      errors.push({ file: filePath, section: id, error: "nested SHARED marker inside block" });
      continue;
    }

    if (mode === "check") {
      // 比對 marker 中的版本號與 canonical core_version
      const markerVersionMatch = beginMatch[0].match(/v(\d+)/);
      if (markerVersionMatch && parseInt(markerVersionMatch[1], 10) !== canonical.coreVersion) {
        errors.push({
          file: filePath,
          section: id,
          error: `version mismatch: marker v${markerVersionMatch[1]} but canonical core_version is ${canonical.coreVersion}`,
        });
      }

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

  // processFile 是純函式：不寫檔，寫入一律由 main() 在全部 consumer 驗證通過後統一執行
  return { errors, content };
}

function main() {
  const args = process.argv.slice(2);

  // M9: CLI 驗證——只允許 --check / --write / --help / 無參數
  const ALLOWED = new Set(["--check", "--write", "--help"]);
  const unknown = args.filter((a) => !ALLOWED.has(a));
  if (unknown.length > 0) {
    console.error(`Unknown argument(s): ${unknown.join(", ")}\n`);
    console.log(USAGE);
    process.exit(2);
  }

  if (args.includes("--check") && args.includes("--write")) {
    console.error("Error: --check and --write are mutually exclusive.\n");
    console.log(USAGE);
    process.exit(2);
  }

  if (args.includes("--help")) {
    console.log(USAGE);
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

  // M7: --write 兩階段——先 dry-run 全部 consumer，全過才逐檔寫入
  if (mode === "write") {
    const dryRunResults = [];
    let hasErrors = false;

    for (const consumer of CONSUMERS) {
      const result = processFile(consumer.file, consumer.sections, canonical, "write");
      // processFile 回傳 array 表示讀檔或 marker 階段就失敗
      if (Array.isArray(result)) {
        dryRunResults.push({ consumer, errors: result, content: null });
        hasErrors = true;
      } else {
        dryRunResults.push({ consumer, errors: result.errors, content: result.content });
        if (result.errors.length > 0) hasErrors = true;
      }
    }

    if (hasErrors) {
      console.error("Dry-run failed — no files written.\n");
      for (const { consumer, errors } of dryRunResults) {
        for (const err of errors) {
          console.error(`  FAIL  ${err.file} [${err.section}]: ${err.error}`);
        }
      }
      const totalErrors = dryRunResults.reduce((s, r) => s + r.errors.length, 0);
      console.error(`\n${totalErrors} error(s) found during dry-run.`);
      process.exit(1);
    }

    // 全過——逐檔寫入
    for (const { consumer, content } of dryRunResults) {
      const abs = resolve(ROOT, consumer.file);
      writeFileSync(abs, content, "utf8");
      console.log(`  WROTE ${consumer.file} (${consumer.sections.length} sections)`);
    }

    console.log(`\nAll blocks written successfully.`);
    process.exit(0);
  }

  // --check 模式
  let allErrors = [];

  for (const consumer of CONSUMERS) {
    const result = processFile(consumer.file, consumer.sections, canonical, mode);
    const errors = Array.isArray(result) ? result : result.errors;
    allErrors = allErrors.concat(errors);

    if (errors.length === 0) {
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
    console.log(`All blocks verified successfully.`);
    process.exit(0);
  }
}

main();
