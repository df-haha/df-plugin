const assert = require('node:assert/strict');
const { readFileSync, readdirSync, lstatSync, existsSync } = require('node:fs');
const { join, resolve } = require('node:path');
const test = require('node:test');

const repoRoot = resolve(__dirname, '..', '..', '..');
const pluginRoot = join(repoRoot, 'plugins', 'pilotfish-roles');
const templatesRoot = join(pluginRoot, 'skills', 'pilotfish-setup', 'templates');

function read(relativePath) {
  return readFileSync(join(repoRoot, relativePath), 'utf8');
}

function readJson(relativePath) {
  return JSON.parse(read(relativePath));
}

function frontmatter(markdown) {
  const match = /^---\n([\s\S]*?)\n---\n/.exec(markdown);
  assert.ok(match, 'markdown must start with YAML frontmatter');
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

test('publishes a Claude Code manifest and registers in the Claude marketplace only', () => {
  const manifest = readJson('plugins/pilotfish-roles/.claude-plugin/plugin.json');
  assert.equal(manifest.name, 'pilotfish-roles');
  assert.equal(manifest.version, '1.0.0');
  assert.deepEqual(manifest.author, { name: 'df-haha' });

  const marketplace = readJson('.claude-plugin/marketplace.json');
  const entry = marketplace.plugins.find((plugin) => plugin.name === 'pilotfish-roles');
  assert.ok(entry, 'pilotfish-roles must be registered in .claude-plugin/marketplace.json');
  assert.equal(entry.version, manifest.version);
  assert.equal(entry.source, './plugins/pilotfish-roles');

  // 本 skill 寫 ~/.claude/ 路徑，Codex 宿主無法使用 — 不得登記到 Codex 側 marketplace
  assert.equal(existsSync(join(pluginRoot, '.codex-plugin')), false, 'pilotfish-roles is Claude-only');
  const codexMarketplace = read('.agents/plugins/marketplace.json');
  assert.equal(codexMarketplace.includes('pilotfish-roles'), false, 'must not appear in Codex marketplace');
});

test('setup skill declares installer contract in frontmatter', () => {
  const skill = frontmatter(read('plugins/pilotfish-roles/skills/pilotfish-setup/SKILL.md'));
  assert.equal(skill.name, 'pilotfish-setup');
  assert.ok(skill.description.includes('Claude Code 專用'));
  assert.ok(skill.description.includes('pilotfish setup'), 'description must carry trigger words');
  for (const tool of ['Bash', 'Read', 'Write', 'Edit', 'AskUserQuestion']) {
    assert.ok(skill['allowed-tools'].includes(tool), `allowed-tools must include ${tool}`);
  }
});

test('ships all eight agent templates with the expected model routing', () => {
  const expected = {
    'scout.md': { model: 'haiku' },
    'Explore.md': { model: 'haiku' },
    'mech-executor.md': { model: 'sonnet' },
    'executor.md': { model: 'opus' },
    'verifier.md': { model: 'opus' },
    'security-executor.md': { model: 'opus' },
    'executor-opus47.md': { model: 'claude-opus-4-7[1m]' },
    'executor-opus45.md': { model: 'claude-opus-4-5-20251101' },
  };
  const shipped = readdirSync(join(templatesRoot, 'agents')).sort();
  assert.deepEqual(shipped, Object.keys(expected).sort());

  for (const [file, { model }] of Object.entries(expected)) {
    const agent = frontmatter(readFileSync(join(templatesRoot, 'agents', file), 'utf8'));
    assert.equal(agent.name, file.replace(/\.md$/, ''));
    assert.equal(agent.model, model, `${file} must route to ${model}`);
    // leaf agent 紀律：不是唯讀 allowlist（tools:）就必須 disallow Agent/Workflow
    if (!agent.tools) {
      assert.ok(agent.disallowedTools.includes('Agent'), `${file} must disallow Agent`);
      assert.ok(agent.disallowedTools.includes('Workflow'), `${file} must disallow Workflow`);
    }
  }
});

test('ships the delegation policy template', () => {
  const policy = readFileSync(join(templatesRoot, 'rules', 'agents.md'), 'utf8');
  assert.ok(policy.includes('# Agent 使用規則'));
  assert.ok(policy.includes('ANTHROPIC_DEFAULT_OPUS_MODEL'), 'policy must document env pinning');
  assert.ok(policy.includes('CLAUDE_CODE_SUBAGENT_MODEL'), 'policy must document the forbidden override');
});

test('keeps upstream MIT attribution', () => {
  const license = read('plugins/pilotfish-roles/LICENSE.pilotfish');
  assert.ok(license.includes('MIT License'));
  assert.ok(license.includes('Copyright (c) 2026 Nanako0129'));
  assert.ok(read('plugins/pilotfish-roles/README.md').includes('Nanako0129/pilotfish'));
});

test('contains no symlinks', () => {
  assert.ok(walkFiles(pluginRoot).length > 0);
});
