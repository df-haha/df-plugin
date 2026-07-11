const assert = require('node:assert/strict');
const { readFileSync, readdirSync, lstatSync } = require('node:fs');
const { join, resolve } = require('node:path');
const test = require('node:test');

const repoRoot = resolve(__dirname, '..', '..', '..');
const pluginRoot = join(repoRoot, 'plugins', 'decision-wiki');

function read(relativePath) {
  return readFileSync(join(repoRoot, relativePath), 'utf8');
}

function readJson(relativePath) {
  return JSON.parse(read(relativePath));
}

function frontmatter(markdown) {
  const match = /^---\n([\s\S]*?)\n---\n/.exec(markdown);
  assert.ok(match, 'SKILL.md must start with YAML frontmatter');
  return Object.fromEntries(
    match[1]
      .split('\n')
      .filter(Boolean)
      .map((line) => {
        const separator = line.indexOf(':');
        assert.notEqual(separator, -1, `invalid frontmatter line: ${line}`);
        return [line.slice(0, separator), line.slice(separator + 1).trim()];
      }),
  );
}

function walkFiles(directory) {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const absolutePath = join(directory, entry.name);
    assert.equal(lstatSync(absolutePath).isSymbolicLink(), false, `symlink is not portable: ${absolutePath}`);
    return entry.isDirectory() ? walkFiles(absolutePath) : [absolutePath];
  });
}

test('publishes matching Claude Code and Codex manifests', () => {
  const claude = readJson('plugins/decision-wiki/.claude-plugin/plugin.json');
  const codex = readJson('plugins/decision-wiki/.codex-plugin/plugin.json');

  assert.equal(claude.name, 'decision-wiki');
  assert.equal(codex.name, 'decision-wiki');
  assert.equal(claude.version, '1.0.0');
  assert.equal(codex.version, claude.version);
  assert.deepEqual(claude.author, { name: 'df-haha' });
  assert.deepEqual(codex.author, { name: 'df-haha' });
  assert.equal(codex.skills, './skills/');
  assert.equal('hooks' in codex, false);
  assert.equal('mcpServers' in codex, false);
  assert.equal('apps' in codex, false);
});

test('registers exactly one entry in each marketplace', () => {
  const claude = readJson('.claude-plugin/marketplace.json');
  const codex = readJson('.agents/plugins/marketplace.json');
  const claudeEntries = claude.plugins.filter((entry) => entry.name === 'decision-wiki');
  const codexEntries = codex.plugins.filter((entry) => entry.name === 'decision-wiki');

  assert.equal(claudeEntries.length, 1);
  assert.equal(claudeEntries[0].source, './plugins/decision-wiki');
  assert.equal(claudeEntries[0].version, '1.0.0');
  assert.equal(claudeEntries[0].category, 'development');

  assert.equal(codexEntries.length, 1);
  assert.deepEqual(codexEntries[0].source, {
    source: 'local',
    path: './plugins/decision-wiki',
  });
  assert.deepEqual(codexEntries[0].policy, {
    installation: 'AVAILABLE',
    authentication: 'ON_INSTALL',
  });
  assert.equal(codexEntries[0].category, 'Development');
});

test('packages both complete cross-client skill trees', () => {
  const requiredFiles = [
    'plugins/decision-wiki/skills/setup-decision-wiki/SKILL.md',
    'plugins/decision-wiki/skills/setup-decision-wiki/agents/openai.yaml',
    'plugins/decision-wiki/skills/setup-decision-wiki/assets/docs/decisions/README.md',
    'plugins/decision-wiki/skills/setup-decision-wiki/assets/docs/decisions/INDEX.md',
    'plugins/decision-wiki/skills/setup-decision-wiki/assets/docs/decisions/_draft/README.md',
    'plugins/decision-wiki/skills/setup-decision-wiki/assets/scripts/validate-decisions.mjs',
    'plugins/decision-wiki/skills/setup-decision-wiki/assets/tests/validate-decisions.test.mjs',
    'plugins/decision-wiki/skills/save-decision/SKILL.md',
    'plugins/decision-wiki/skills/save-decision/agents/openai.yaml',
  ];

  for (const relativePath of requiredFiles) {
    assert.ok(read(relativePath).length > 0, `missing or empty: ${relativePath}`);
  }

  for (const [skill, expectedName] of [
    ['setup-decision-wiki', 'setup-decision-wiki'],
    ['save-decision', 'save-decision'],
  ]) {
    const metadata = frontmatter(read(`plugins/decision-wiki/skills/${skill}/SKILL.md`));
    assert.deepEqual(Object.keys(metadata).sort(), ['description', 'name']);
    assert.equal(metadata.name, expectedName);
    assert.match(metadata.description, /^Use when /);
  }
});

test('contains no host-specific frontmatter, local paths, or symlinks', () => {
  const skillRoot = join(pluginRoot, 'skills');
  const forbidden = /allowed-tools|\/home\/haha|CC_project\/CC-memory|cc_memory_save|decision_card_validate|decision_index_gen|superseded_by/;

  for (const absolutePath of walkFiles(skillRoot)) {
    const content = readFileSync(absolutePath, 'utf8');
    assert.doesNotMatch(content, forbidden, absolutePath);
  }
});

test('documents both hosts and both skills', () => {
  const readme = read('README.md');
  assert.match(readme, /decision-wiki/);
  assert.match(readme, /Claude Code/);
  assert.match(readme, /Codex/);
  assert.match(readme, /setup-decision-wiki/);
  assert.match(readme, /save-decision/);
});
