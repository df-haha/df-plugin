import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { createHash } from 'node:crypto';
import { afterEach, test } from 'node:test';
import { mkdtemp, mkdir, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { validateDecisionWiki } from '../scripts/validate-decisions.mjs';

const tempRepos = [];
const defaultExcerpt = 'The human approved this decision.\nImplement it now.';
const validatorPath = fileURLToPath(new URL('../scripts/validate-decisions.mjs', import.meta.url));

function sha256(value) {
  return createHash('sha256').update(value, 'utf8').digest('hex');
}

function inlineArray(values = []) {
  return `[${values.join(', ')}]`;
}

function makeCard({
  id,
  title = 'Valid card',
  status = 'active',
  excerpt = defaultExcerpt,
  excerptSha256 = sha256(excerpt),
  verified = true,
  supersedes = [],
  dependsOn = [],
  conflictsWith = [],
  relatedTo = [],
  extraFrontmatter = '',
  sourceRef = 'user-confirmation-2026-07-11',
} = {}) {
  const optionalFrontmatter = extraFrontmatter === '' ? '' : `${extraFrontmatter}\n`;
  const quotedExcerpt = excerpt.split('\n').map((line) => `> ${line}`).join('\n');

  return `---
id: ${id}
title: ${title}
status: ${status}
decided_at: 2026-07-11T20:00:00+08:00
scope: test
supersedes: ${inlineArray(supersedes)}
depends_on: ${inlineArray(dependsOn)}
conflicts_with: ${inlineArray(conflictsWith)}
related_to: ${inlineArray(relatedTo)}
${optionalFrontmatter}sources:
  - id: S1
    type: manual
    client: human
    ref: ${sourceRef}
    captured_at: 2026-07-11T20:00:00+08:00
    excerpt_sha256: ${excerptSha256}
    verified: ${verified}
---

# ${title}

## 決策背景與決策前狀態

Context.

## 替代方案及採否理由

Alternatives.

## 最終決策與理由

Decision.

## 預期後果及決策後狀態

Consequences.

## 原文溯源

### S1

${quotedExcerpt}

## 後續結果與沿革

None yet.
`;
}

async function createRepo() {
  const repoRoot = await mkdtemp(join(tmpdir(), 'portable-decisions-'));
  tempRepos.push(repoRoot);
  await mkdir(join(repoRoot, 'docs', 'decisions', '_draft'), { recursive: true });
  await writeFile(join(repoRoot, 'docs', 'decisions', 'README.md'), '# 決策 Wiki 規範\n');
  await writeFile(join(repoRoot, 'docs', 'decisions', '_draft', 'README.md'), '# 決策候選區\n');
  return repoRoot;
}

async function writeCard(repoRoot, relativePath, card) {
  const file = join(repoRoot, 'docs', 'decisions', relativePath);
  await mkdir(dirname(file), { recursive: true });
  await writeFile(file, card);
}

async function writeIndexRows(repoRoot, rows = '') {
  await writeFile(
    join(repoRoot, 'docs', 'decisions', 'INDEX.md'),
    `# 決策索引\n\n## 正式決策\n\n| ID | 狀態 | 日期 | 標題 | 決策卡 |\n|---|---|---|---|---|\n${rows}`,
  );
}

async function writeIndex(repoRoot, cards = []) {
  const rows = cards.map(({ id, status = 'active', target = `./${id}.md` }) => (
    `| \`${id}\` | ${status} | 2026-07-11 | Valid card | [開啟](${target}) |\n`
  )).join('');
  await writeIndexRows(repoRoot, rows);
}

async function runCli(repoRoot) {
  const child = spawn(process.execPath, [validatorPath, repoRoot], { cwd: repoRoot });
  let stderr = '';
  let stdout = '';
  child.stderr.setEncoding('utf8');
  child.stdout.setEncoding('utf8');
  child.stderr.on('data', (chunk) => { stderr += chunk; });
  child.stdout.on('data', (chunk) => { stdout += chunk; });

  return new Promise((resolvePromise, reject) => {
    child.once('error', reject);
    child.once('close', (code) => resolvePromise({ code, stderr, stdout }));
  });
}

function issueCodes(report) {
  return report.issues.map(({ code }) => code);
}

afterEach(async () => {
  await Promise.all(tempRepos.splice(0).map((repoRoot) => rm(repoRoot, {
    force: true,
    recursive: true,
  })));
});

test('accepts the zero-card skeleton', async () => {
  const repoRoot = await createRepo();
  await writeIndex(repoRoot);

  assert.deepEqual(await validateDecisionWiki(repoRoot), {
    ok: true,
    cards: 0,
    issues: [],
  });
});

test('accepts a valid formal decision card', async () => {
  const repoRoot = await createRepo();
  const id = 'DEC-20260711T120000Z-valid-card';
  await writeCard(repoRoot, `${id}.md`, makeCard({ id }));
  await writeIndex(repoRoot, [{ id }]);

  assert.deepEqual(await validateDecisionWiki(repoRoot), {
    ok: true,
    cards: 1,
    issues: [],
  });
});

test('reports source excerpt hash drift', async () => {
  const repoRoot = await createRepo();
  const id = 'DEC-20260711T120001Z-source-drift';
  await writeCard(repoRoot, `${id}.md`, makeCard({
    id,
    excerptSha256: '0'.repeat(64),
  }));
  await writeIndex(repoRoot, [{ id }]);

  assert.ok(issueCodes(await validateDecisionWiki(repoRoot)).includes('SOURCE_HASH_MISMATCH'));
});

test('rejects duplicate visible source headings', async () => {
  const repoRoot = await createRepo();
  const id = 'DEC-20260711T120015Z-duplicate-source-heading';
  const quotedExcerpt = defaultExcerpt.split('\n').map((line) => `> ${line}`).join('\n');
  const sourceBlock = `### S1\n\n${quotedExcerpt}`;
  const card = makeCard({ id }).replace(
    sourceBlock,
    `${sourceBlock}\n\n### S1\n\n${quotedExcerpt}`,
  );
  await writeCard(repoRoot, `${id}.md`, card);
  await writeIndex(repoRoot, [{ id }]);

  assert.ok(issueCodes(await validateDecisionWiki(repoRoot)).includes(
    'SOURCE_HEADING_DUPLICATE',
  ));
});

test('rejects an undeclared visible source heading', async () => {
  const repoRoot = await createRepo();
  const id = 'DEC-20260711T120016Z-undeclared-source-heading';
  const card = makeCard({ id }).replace(
    '## 後續結果與沿革',
    '### S2\n\n> Orphan excerpt.\n\n## 後續結果與沿革',
  );
  await writeCard(repoRoot, `${id}.md`, card);
  await writeIndex(repoRoot, [{ id }]);

  assert.ok(issueCodes(await validateDecisionWiki(repoRoot)).includes(
    'SOURCE_HEADING_UNDECLARED',
  ));
});

for (const field of ['similarity', 'semantic_similarity', 'similar_to']) {
  test(`forbids persisted ${field}`, async () => {
    const repoRoot = await createRepo();
    const id = `DEC-20260711T120002Z-${field.replaceAll('_', '-')}`;
    await writeCard(repoRoot, `${id}.md`, makeCard({
      id,
      extraFrontmatter: `${field}: 0.95`,
    }));
    await writeIndex(repoRoot, [{ id }]);

    assert.ok(issueCodes(await validateDecisionWiki(repoRoot)).includes(
      'FORBIDDEN_SIMILARITY_FIELD',
    ));
  });
}

test('rejects a formal relation to a proposed draft', async () => {
  const repoRoot = await createRepo();
  const formalId = 'DEC-20260711T120003Z-formal-card';
  const draftId = 'DEC-20260711T120004Z-draft-card';
  await writeCard(repoRoot, `_draft/${draftId}.md`, makeCard({
    id: draftId,
    status: 'proposed',
  }));
  await writeCard(repoRoot, `${formalId}.md`, makeCard({
    id: formalId,
    relatedTo: [draftId],
  }));
  await writeIndex(repoRoot, [{ id: formalId }]);

  assert.ok(issueCodes(await validateDecisionWiki(repoRoot)).includes('DANGLING_RELATION'));
});

test('accepts a formal relation to an ADR at any existing path under docs', async () => {
  const repoRoot = await createRepo();
  const id = 'DEC-20260711T120019Z-related-to-legacy-adr';
  const adrDirectory = join(repoRoot, 'docs', 'adr');
  await mkdir(adrDirectory, { recursive: true });
  await writeFile(join(adrDirectory, 'ADR-001-existing-decision.md'), '# Existing ADR\n');
  await writeCard(repoRoot, `${id}.md`, makeCard({
    id,
    relatedTo: ['ADR-001'],
  }));
  await writeIndex(repoRoot, [{ id }]);

  assert.deepEqual(await validateDecisionWiki(repoRoot), {
    ok: true,
    cards: 1,
    issues: [],
  });
});

test('accepts a supersedes edge when the target is superseded', async () => {
  const repoRoot = await createRepo();
  const oldId = 'DEC-20260711T120005Z-old-decision';
  const newId = 'DEC-20260711T120006Z-new-decision';
  await writeCard(repoRoot, `${oldId}.md`, makeCard({ id: oldId, status: 'superseded' }));
  await writeCard(repoRoot, `${newId}.md`, makeCard({ id: newId, supersedes: [oldId] }));
  await writeIndex(repoRoot, [
    { id: oldId, status: 'superseded' },
    { id: newId },
  ]);

  assert.deepEqual(await validateDecisionWiki(repoRoot), {
    ok: true,
    cards: 2,
    issues: [],
  });
});

test('rejects supersedes when the target remains active', async () => {
  const repoRoot = await createRepo();
  const oldId = 'DEC-20260711T120007Z-still-active';
  const newId = 'DEC-20260711T120008Z-replacement';
  await writeCard(repoRoot, `${oldId}.md`, makeCard({ id: oldId }));
  await writeCard(repoRoot, `${newId}.md`, makeCard({ id: newId, supersedes: [oldId] }));
  await writeIndex(repoRoot, [{ id: oldId }, { id: newId }]);

  assert.ok(issueCodes(await validateDecisionWiki(repoRoot)).includes(
    'SUPERSEDES_STATUS_MISMATCH',
  ));
});

test('rejects a superseded card without an incoming supersedes edge', async () => {
  const repoRoot = await createRepo();
  const id = 'DEC-20260711T120009Z-orphaned-superseded';
  await writeCard(repoRoot, `${id}.md`, makeCard({ id, status: 'superseded' }));
  await writeIndex(repoRoot, [{ id, status: 'superseded' }]);

  assert.ok(issueCodes(await validateDecisionWiki(repoRoot)).includes(
    'SUPERSEDES_STATUS_MISMATCH',
  ));
});

for (const [description, target] of [
  ['a URI', 'https://example.com/decision.md'],
  ['an absolute path', '/tmp/decision.md'],
  ['path traversal', '../archive/decision.md'],
  ['another relative directory', './archive/decision.md'],
]) {
  test(`requires an exact local INDEX link instead of ${description}`, async () => {
    const repoRoot = await createRepo();
    const id = 'DEC-20260711T120010Z-index-link';
    await writeCard(repoRoot, `${id}.md`, makeCard({ id }));
    await writeIndex(repoRoot, [{ id, target }]);

    assert.ok(issueCodes(await validateDecisionWiki(repoRoot)).includes('INDEX_LINK_INVALID'));
  });
}

test('rejects duplicate visible INDEX rows', async () => {
  const repoRoot = await createRepo();
  const id = 'DEC-20260711T120011Z-duplicate-index';
  const row = `| \`${id}\` | active | 2026-07-11 | Valid card | [開啟](./${id}.md) |\n`;
  await writeCard(repoRoot, `${id}.md`, makeCard({ id }));
  await writeIndexRows(repoRoot, `${row}${row}`);

  assert.ok(issueCodes(await validateDecisionWiki(repoRoot)).includes('INDEX_ENTRY_DUPLICATE'));
});

test('rejects an unknown DEC row in INDEX', async () => {
  const repoRoot = await createRepo();
  const id = 'DEC-20260711T120012Z-unknown-index';
  await writeIndex(repoRoot, [{ id }]);

  assert.ok(issueCodes(await validateDecisionWiki(repoRoot)).includes('INDEX_ENTRY_STALE'));
});

test('rejects a proposed draft row in INDEX', async () => {
  const repoRoot = await createRepo();
  const id = 'DEC-20260711T120013Z-indexed-draft';
  await writeCard(repoRoot, `_draft/${id}.md`, makeCard({ id, status: 'proposed' }));
  await writeIndex(repoRoot, [{ id, status: 'proposed' }]);

  assert.ok(issueCodes(await validateDecisionWiki(repoRoot)).includes('INDEX_ENTRY_STALE'));
});

test('rejects a malformed DEC-like ID in INDEX', async () => {
  const repoRoot = await createRepo();
  const malformedId = 'DEC-20260711T120017Z-Bad-Slug';
  await writeIndexRows(
    repoRoot,
    `| \`${malformedId}\` | active | 2026-07-11 | Invalid ID | [開啟](./${malformedId}.md) |\n`,
  );

  assert.ok(issueCodes(await validateDecisionWiki(repoRoot)).includes('INDEX_ID_INVALID'));
});

test('rejects an INDEX status that differs from the formal card', async () => {
  const repoRoot = await createRepo();
  const id = 'DEC-20260711T120018Z-index-status-mismatch';
  await writeCard(repoRoot, `${id}.md`, makeCard({ id, status: 'active' }));
  await writeIndex(repoRoot, [{ id, status: 'archived' }]);

  assert.ok(issueCodes(await validateDecisionWiki(repoRoot)).includes(
    'INDEX_STATUS_MISMATCH',
  ));
});

test('CLI prints JSON and exits nonzero for an invalid repository', async () => {
  const repoRoot = await createRepo();
  const id = 'DEC-20260711T120014Z-cli-invalid';
  await writeCard(repoRoot, `${id}.md`, makeCard({ id }));
  await writeIndex(repoRoot);

  const result = await runCli(repoRoot);

  assert.equal(result.code, 1);
  assert.equal(result.stderr, '');
  assert.deepEqual(JSON.parse(result.stdout), {
    ok: false,
    cards: 1,
    issues: [
      {
        code: 'INDEX_ENTRY_MISSING',
        file: `docs/decisions/${id}.md`,
        message: `formal decision ${id} is missing from INDEX.md`,
      },
    ],
  });
});
