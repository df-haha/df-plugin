const assert = require('node:assert/strict');
const { spawnSync } = require('node:child_process');
const { existsSync, lstatSync, readFileSync, readdirSync } = require('node:fs');
const { join, resolve } = require('node:path');
const test = require('node:test');

const repoRoot = resolve(__dirname, '..', '..', '..');
const pluginRoot = join(repoRoot, 'plugins', 'hermes-exchange');

function read(relativePath) {
  return readFileSync(join(repoRoot, relativePath), 'utf8');
}

function readJson(relativePath) {
  return JSON.parse(read(relativePath));
}

function walkFiles(directory) {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const absolutePath = join(directory, entry.name);
    assert.equal(lstatSync(absolutePath).isSymbolicLink(), false, `symlink is not portable: ${absolutePath}`);
    return entry.isDirectory() ? walkFiles(absolutePath) : [absolutePath];
  });
}

test('publishes matching Claude Code and Codex manifests', () => {
  const claude = readJson('plugins/hermes-exchange/.claude-plugin/plugin.json');
  const codex = readJson('plugins/hermes-exchange/.codex-plugin/plugin.json');

  assert.equal(claude.name, 'hermes-exchange');
  assert.equal(codex.name, claude.name);
  assert.equal(codex.version, claude.version);
  assert.equal(claude.author.name, 'df-haha');
  assert.equal(codex.author.name, 'df-haha');
  assert.equal(codex.skills, './skills/');
  assert.equal('hooks' in codex, false);
  assert.equal('mcpServers' in codex, false);
  assert.equal('apps' in codex, false);
});

test('registers exactly one entry in each marketplace', () => {
  const claudeEntries = readJson('.claude-plugin/marketplace.json').plugins
    .filter((entry) => entry.name === 'hermes-exchange');
  const codexEntries = readJson('.agents/plugins/marketplace.json').plugins
    .filter((entry) => entry.name === 'hermes-exchange');

  assert.equal(claudeEntries.length, 1);
  assert.equal(claudeEntries[0].source, './plugins/hermes-exchange');
  assert.equal(claudeEntries[0].version, '0.1.0');
  assert.equal(claudeEntries[0].category, 'development');

  assert.equal(codexEntries.length, 1);
  assert.deepEqual(codexEntries[0].source, {
    source: 'local',
    path: './plugins/hermes-exchange',
  });
  assert.deepEqual(codexEntries[0].policy, {
    installation: 'AVAILABLE',
    authentication: 'ON_INSTALL',
  });
  assert.equal(codexEntries[0].category, 'Development');
});

test('packages the user-scope runtime, setup skill, and protocol references without symlinks', () => {
  const requiredFiles = [
    'plugins/hermes-exchange/assets/hermes_exchange/plugin.yaml',
    'plugins/hermes-exchange/assets/hermes_exchange/__init__.py',
    'plugins/hermes-exchange/assets/hermes_exchange/envelope.py',
    'plugins/hermes-exchange/assets/hermes_exchange/workflow.py',
    'plugins/hermes-exchange/skills/hermes-exchange-setup/SKILL.md',
    'plugins/hermes-exchange/skills/hermes-exchange-setup/agents/openai.yaml',
    'plugins/hermes-exchange/skills/hermes-exchange-setup/references/protocol-v1.md',
    'plugins/hermes-exchange/skills/hermes-exchange-setup/references/adapter-guide.md',
    'plugins/hermes-exchange/skills/hermes-exchange-setup/scripts/install_hermes_exchange_user_plugin.py',
  ];

  for (const relativePath of requiredFiles) {
    assert.ok(read(relativePath).length > 0, `missing or empty: ${relativePath}`);
  }
  assert.ok(walkFiles(pluginRoot).length >= requiredFiles.length);
});

test('documents protocol compatibility instead of identical package installation', () => {
  const readme = read('plugins/hermes-exchange/README.md');
  const skill = read('plugins/hermes-exchange/skills/hermes-exchange-setup/SKILL.md');
  const protocol = read(
    'plugins/hermes-exchange/skills/hermes-exchange-setup/references/protocol-v1.md',
  );

  assert.match(readme, /compatible endpoint, not an identical package/i);
  assert.match(skill, /protocol compatibility, not package equality/i);
  assert.match(protocol, /does not need Hermes or this marketplace plugin/i);
  assert.match(skill, /<skill-dir>\/scripts\/install_hermes_exchange_user_plugin\.py/);

  const skillDir = join(pluginRoot, 'skills', 'hermes-exchange-setup');
  const bundledPaths = [...skill.matchAll(/<skill-dir>\/([A-Za-z0-9_./-]+)/g)]
    .map((match) => match[1]);
  assert.ok(bundledPaths.length >= 4, 'expected every bundled skill path to use <skill-dir>');
  for (const relativePath of bundledPaths) {
    assert.equal(
      existsSync(resolve(skillDir, relativePath)),
      true,
      `SKILL.md bundled path does not exist: <skill-dir>/${relativePath}`,
    );
  }
});

test('runs the dependency-free installer and protocol contract suite', () => {
  const result = spawnSync(
    'python3',
    ['-m', 'unittest', 'discover', '-s', 'plugins/hermes-exchange/tests', '-p', 'test_*.py'],
    { cwd: repoRoot, encoding: 'utf8' },
  );

  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
  assert.match(result.stderr, /Ran 15 tests/);
  assert.match(result.stderr, /OK/);
});
