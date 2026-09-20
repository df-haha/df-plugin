const assert = require('node:assert/strict');
const { spawnSync } = require('node:child_process');
const { existsSync, lstatSync, readFileSync, readdirSync } = require('node:fs');
const { join, resolve } = require('node:path');
const test = require('node:test');

const repoRoot = resolve(__dirname, '..', '..', '..');
const pluginRoot = join(repoRoot, 'plugins', 'hermes-collaboration');

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

test('publishes matching Claude Code and Codex manifests and marketplace entries', () => {
  const claude = readJson('plugins/hermes-collaboration/.claude-plugin/plugin.json');
  const codex = readJson('plugins/hermes-collaboration/.codex-plugin/plugin.json');
  const claudeEntries = readJson('.claude-plugin/marketplace.json').plugins
    .filter((entry) => entry.name === 'hermes-collaboration');
  const codexEntries = readJson('.agents/plugins/marketplace.json').plugins
    .filter((entry) => entry.name === 'hermes-collaboration');

  assert.equal(claude.name, 'hermes-collaboration');
  assert.equal(codex.name, claude.name);
  assert.equal(codex.version, claude.version);
  assert.equal(codex.skills, './skills/');
  assert.equal('hooks' in codex, false);
  assert.equal('mcpServers' in codex, false);
  assert.equal('apps' in codex, false);

  assert.equal(claudeEntries.length, 1);
  assert.equal(claudeEntries[0].source, './plugins/hermes-collaboration');
  assert.equal(claudeEntries[0].version, claude.version);
  assert.equal(claudeEntries[0].category, 'development');

  assert.equal(codexEntries.length, 1);
  assert.deepEqual(codexEntries[0].source, {
    source: 'local',
    path: './plugins/hermes-collaboration',
  });
  assert.deepEqual(codexEntries[0].policy, {
    installation: 'AVAILABLE',
    authentication: 'ON_INSTALL',
  });
  assert.equal(codexEntries[0].category, 'Development');
});

test('packages the complete reviewed runtime and both skills without generated files', () => {
  const requiredFiles = [
    'plugins/hermes-collaboration/assets/hermes_collaboration/plugin.yaml',
    'plugins/hermes-collaboration/assets/hermes_collaboration/__init__.py',
    'plugins/hermes-collaboration/assets/hermes_collaboration/config.example.yaml',
    'plugins/hermes-collaboration/assets/hermes_collaboration/contracts/case-command.schema.json',
    'plugins/hermes-collaboration/assets/hermes_collaboration/contracts/standing-analysis-policy.schema.json',
    'plugins/hermes-collaboration/skills/hermes-collaboration-setup/SKILL.md',
    'plugins/hermes-collaboration/skills/hermes-collaboration-setup/agents/openai.yaml',
    'plugins/hermes-collaboration/skills/hermes-collaboration-setup/scripts/install_hermes_collaboration_user_plugin.py',
    'plugins/hermes-collaboration/skills/hermes-collaboration-operations/SKILL.md',
    'plugins/hermes-collaboration/skills/hermes-collaboration-operations/agents/openai.yaml',
  ];

  for (const relativePath of requiredFiles) {
    assert.ok(read(relativePath).length > 0, `missing or empty: ${relativePath}`);
  }
  const packaged = walkFiles(pluginRoot);
  assert.ok(packaged.length >= requiredFiles.length);
  assert.equal(packaged.some((path) => path.includes('__pycache__') || path.endsWith('.pyc')), false);
  assert.equal(
    readdirSync(join(pluginRoot, 'assets', 'hermes_collaboration', 'contracts'))
      .filter((name) => name.endsWith('.schema.json')).length,
    11,
  );
});

test('setup keeps installation, configuration, enablement, restart, and live traffic separate', () => {
  const skill = read('plugins/hermes-collaboration/skills/hermes-collaboration-setup/SKILL.md');

  assert.match(skill, /<skill-dir>\/scripts\/install_hermes_collaboration_user_plugin\.py/);
  assert.match(skill, /does not .*enable.*restart.*send/is);
  assert.match(skill, /numeric bot ID/i);
  assert.match(skill, /TELEGRAM_BOT_TOKEN/);
  assert.match(skill, /11 schemas/i);
  assert.match(skill, /--replace/);
  assert.match(skill, /rollback/i);
  assert.match(skill, /separate authorization/i);

  const skillDir = join(pluginRoot, 'skills', 'hermes-collaboration-setup');
  const bundledPaths = [...skill.matchAll(/<skill-dir>\/([A-Za-z0-9_./-]+)/g)]
    .map((match) => match[1]);
  assert.ok(bundledPaths.length >= 3, 'expected bundled setup paths to use <skill-dir>');
  for (const relativePath of bundledPaths) {
    assert.equal(existsSync(resolve(skillDir, relativePath)), true, `missing: <skill-dir>/${relativePath}`);
  }
});

test('operations preserves authority boundaries across the case lifecycle', () => {
  const skill = read('plugins/hermes-collaboration/skills/hermes-collaboration-operations/SKILL.md');

  assert.match(skill, /HERMES_CASE_V1/);
  assert.match(skill, /Discussion Mandate.*never.*Execution Grant/is);
  assert.match(skill, /pull request.*never.*merge authority/is);
  assert.match(skill, /remote.*untrusted/is);
  assert.match(skill, /case_view/);
  assert.match(skill, /case_list/);
  assert.match(skill, /case_run_work/);
  assert.match(skill, /repository mutation.*explicit Execution Grant/is);
  assert.match(skill, /Installing this marketplace package does not expose those tools directly/i);
  assert.match(skill, /tools are absent.*do not invent.*bypass/is);
  assert.match(skill, /Never claim.*inspected or changed.*tool returned/is);
});

test('bundled installer validates and installs into an isolated profile', () => {
  const installer = join(
    pluginRoot,
    'skills',
    'hermes-collaboration-setup',
    'scripts',
    'install_hermes_collaboration_user_plugin.py',
  );
  const result = spawnSync(
    'python3',
    ['-m', 'unittest', 'discover', '-s', 'plugins/hermes-collaboration/tests', '-p', 'test_*.py'],
    {
      cwd: repoRoot,
      encoding: 'utf8',
      env: { ...process.env, PYTHONDONTWRITEBYTECODE: '1' },
    },
  );

  assert.ok(existsSync(installer));
  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
  assert.match(result.stderr, /Ran \d+ tests/);
  assert.match(result.stderr, /OK/);
});
