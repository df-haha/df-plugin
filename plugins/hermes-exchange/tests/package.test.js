const assert = require('node:assert/strict');
const { spawnSync } = require('node:child_process');
const { existsSync, lstatSync, readFileSync, readdirSync } = require('node:fs');
const { join, resolve } = require('node:path');
const test = require('node:test');

const repoRoot = resolve(__dirname, '..', '..', '..');
const pluginRoot = join(repoRoot, 'plugins', 'hermes-exchange');
const sendWrapper = join(
  pluginRoot,
  'skills',
  'hermes-exchange-send',
  'scripts',
  'send_notification.py',
);

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

test('publishes matching lightweight-relay manifests and marketplace entries', () => {
  const claude = readJson('plugins/hermes-exchange/.claude-plugin/plugin.json');
  const codex = readJson('plugins/hermes-exchange/.codex-plugin/plugin.json');
  const claudeEntries = readJson('.claude-plugin/marketplace.json').plugins
    .filter((entry) => entry.name === 'hermes-exchange');
  const codexEntries = readJson('.agents/plugins/marketplace.json').plugins
    .filter((entry) => entry.name === 'hermes-exchange');

  assert.equal(claude.name, 'hermes-exchange');
  assert.equal(codex.name, claude.name);
  assert.equal(codex.version, claude.version);
  assert.equal(claude.author.name, 'df-haha');
  assert.equal(codex.author.name, 'df-haha');
  assert.equal(codex.skills, './skills/');
  assert.equal('hooks' in codex, false);
  assert.equal('mcpServers' in codex, false);
  assert.equal('apps' in codex, false);
  for (const description of [claude.description, codex.description]) {
    assert.match(description, /notification relay/i);
    assert.doesNotMatch(description, /task exchange|request and result/i);
  }

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

test('packages the user-scope runtime plus setup and send skills without symlinks', () => {
  const requiredFiles = [
    'plugins/hermes-exchange/assets/hermes_exchange/plugin.yaml',
    'plugins/hermes-exchange/assets/hermes_exchange/__init__.py',
    'plugins/hermes-exchange/skills/hermes-exchange-setup/SKILL.md',
    'plugins/hermes-exchange/skills/hermes-exchange-setup/agents/openai.yaml',
    'plugins/hermes-exchange/skills/hermes-exchange-setup/scripts/install_hermes_exchange_user_plugin.py',
    'plugins/hermes-exchange/skills/hermes-exchange-send/SKILL.md',
    'plugins/hermes-exchange/skills/hermes-exchange-send/agents/openai.yaml',
    'plugins/hermes-exchange/skills/hermes-exchange-send/scripts/send_notification.py',
  ];

  for (const relativePath of requiredFiles) {
    assert.ok(read(relativePath).length > 0, `missing or empty: ${relativePath}`);
  }
  assert.ok(walkFiles(pluginRoot).length >= requiredFiles.length);
});

test('setup documents safe first install, peer allowlisting, and optional execution', () => {
  const readme = read('plugins/hermes-exchange/README.md');
  const skill = read('plugins/hermes-exchange/skills/hermes-exchange-setup/SKILL.md');

  assert.match(readme, /notification relay/i);
  assert.match(skill, /<skill-dir>\/scripts\/install_hermes_exchange_user_plugin\.py/);
  assert.match(skill, /numeric sender ID/i);
  assert.match(skill, /both (?:Telegram )?bots/i);
  assert.match(skill, /BotFather/i);
  assert.match(skill, /execution\.enabled.*false/is);
  assert.match(skill, /absolute path/i);
  assert.match(skill, /claude\|codex/i);
  assert.match(skill, /gateway restart/i);
  assert.match(skill, /does not .*enable.*restart.*configur/is);

  const skillDir = join(pluginRoot, 'skills', 'hermes-exchange-setup');
  const bundledPaths = [...skill.matchAll(/<skill-dir>\/([A-Za-z0-9_./-]+)/g)]
    .map((match) => match[1]);
  assert.ok(bundledPaths.length >= 3, 'expected bundled setup paths to use <skill-dir>');
  for (const relativePath of bundledPaths) {
    assert.equal(
      existsSync(resolve(skillDir, relativePath)),
      true,
      `SKILL.md bundled path does not exist: <skill-dir>/${relativePath}`,
    );
  }
});

test('send guidance treats explicit owner release as the only send authority', () => {
  const skill = read('plugins/hermes-exchange/skills/hermes-exchange-send/SKILL.md');

  assert.match(skill, /explicit(?:ly)? .*user.*(?:authoriz|instruct|release)/is);
  assert.match(skill, /remote.*untrusted/is);
  assert.match(skill, /cannot authorize (?:a )?(?:send|execution)/i);
  assert.match(skill, /HERMES_NOTIFY\/1/);
  assert.match(skill, /send_notification\.py/);
  assert.match(skill, /body.*standard input/is);
  assert.doesNotMatch(skill, /HERMES_EXCHANGE\/1/);
});

test('direct sender imports with the Python standard library alone', () => {
  const help = spawnSync('python3', ['-S', sendWrapper, '--help'], {
    cwd: repoRoot,
    encoding: 'utf8',
  });
  assert.equal(help.status, 0, `${help.stdout}\n${help.stderr}`);
  assert.match(help.stdout, /hermes-relay/);

  const assets = join(pluginRoot, 'assets');
  const configExample = join(assets, 'hermes_exchange', 'config.example.yaml');
  const smoke = spawnSync(
    'python3',
    [
      '-S',
      '-c',
      'from hermes_exchange.config import load_config; c=load_config(__import__("sys").argv[1]); print(c.local_peer)',
      configExample,
    ],
    {
      cwd: repoRoot,
      encoding: 'utf8',
      env: { ...process.env, PYTHONPATH: assets },
    },
  );
  assert.equal(smoke.status, 0, `${smoke.stdout}\n${smoke.stderr}`);
  assert.equal(smoke.stdout.trim(), 'haha');
});

test('runs the dependency-free plugin contract suite', () => {
  const result = spawnSync(
    'python3',
    ['-m', 'unittest', 'discover', '-s', 'plugins/hermes-exchange/tests', '-p', 'test_*.py'],
    { cwd: repoRoot, encoding: 'utf8' },
  );

  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
  assert.match(result.stderr, /Ran \d+ tests/);
  assert.match(result.stderr, /OK/);
});
