const assert = require('node:assert/strict');
const { readFileSync, existsSync } = require('node:fs');
const { join, resolve } = require('node:path');
const test = require('node:test');

const pluginRoot = resolve(__dirname, '..');
const marketRoot = resolve(pluginRoot, '..', '..');

function readJson(p) { return JSON.parse(readFileSync(p, 'utf8')); }

test('claude and codex manifests agree', () => {
  const claude = readJson(join(pluginRoot, '.claude-plugin', 'plugin.json'));
  const codex = readJson(join(pluginRoot, '.codex-plugin', 'plugin.json'));
  assert.equal(claude.name, 'ruling-ledger');
  assert.equal(codex.name, 'ruling-ledger');
  assert.equal(claude.version, codex.version);
  assert.equal(codex.skills, './skills/');
  assert.equal('hooks' in claude, false);
  assert.equal('hooks' in codex, false);
});

test('marketplace registers exactly one ruling-ledger entry', () => {
  const market = readJson(join(marketRoot, '.claude-plugin', 'marketplace.json'));
  assert.equal(market.name, 'df-haha-plugins');
  const entries = market.plugins.filter((e) => e.name === 'ruling-ledger');
  assert.equal(entries.length, 1);
  assert.equal(entries[0].source, './plugins/ruling-ledger');
});

test('required files exist', () => {
  for (const rel of [
    'commands/ledger.md',
    'skills/ruling-ledger/SKILL.md',
    'scripts/ledger_check.py',
    'scripts/ledger_init.py',
    'templates/裁決帳本.md',
    'templates/R-0000.md',
    'templates/claude-md-block.md',
    'README.md',
  ]) {
    assert.ok(existsSync(join(pluginRoot, rel)), `missing: ${rel}`);
  }
});

test('index template has hard zone and 7-column B header', () => {
  const t = readFileSync(join(pluginRoot, 'templates', '裁決帳本.md'), 'utf8');
  assert.ok(t.includes('## 0. 硬禁區'), 'missing hard zone section');
  assert.ok(t.includes('| ID | 狀態 | 命題 | 關鍵字 | 層級 | 復活 | 最近復活 |'), 'B header not 7 columns');
  assert.ok(t.includes('### 已淘汰檔'), 'missing deprecated subtable');
});

test('claude-md-block only carries routing, never pinned stances', () => {
  const t = readFileSync(join(pluginRoot, 'templates', 'claude-md-block.md'), 'utf8');
  assert.equal(/^禁：/m.test(t), false, 'template must not pin stances into CLAUDE.md');
  const ruleLines = t.split('\n').filter((line) => /^\d+\.\s/.test(line));
  assert.equal(ruleLines.length, 3, `expected exactly 3 routing rules, got ${ruleLines.length}`);
});
