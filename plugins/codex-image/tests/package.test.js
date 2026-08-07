const assert = require('node:assert/strict');
const { readFileSync, readdirSync, lstatSync, existsSync } = require('node:fs');
const { join, resolve } = require('node:path');
const test = require('node:test');

const repoRoot = resolve(__dirname, '..', '..', '..');
const pluginRoot = join(repoRoot, 'plugins', 'codex-image');

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
      .filter((line) => line.length > 0 && !/^\s/.test(line))
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
  const claude = readJson('plugins/codex-image/.claude-plugin/plugin.json');
  const codex = readJson('plugins/codex-image/.codex-plugin/plugin.json');

  assert.equal(claude.name, 'codex-image');
  assert.equal(codex.name, 'codex-image');
  assert.equal(claude.version, '1.0.0');
  assert.equal(codex.version.split('+', 1)[0], claude.version);
  assert.match(codex.version, /^1\.0\.0\+codex\.\d{14}$/);
  assert.deepEqual(claude.author, { name: 'df-haha' });
  assert.deepEqual(codex.author, { name: 'df-haha' });
  assert.equal(codex.skills, './skills/');
  assert.equal(codex.interface.category, 'Productivity');
  assert.equal('hooks' in codex, false);
  assert.equal('mcpServers' in codex, false);
  assert.equal('apps' in codex, false);
});

test('registers exactly one entry in each marketplace', () => {
  const claude = readJson('.claude-plugin/marketplace.json');
  const codex = readJson('.agents/plugins/marketplace.json');
  const claudeEntries = claude.plugins.filter((entry) => entry.name === 'codex-image');
  const codexEntries = codex.plugins.filter((entry) => entry.name === 'codex-image');

  assert.equal(claudeEntries.length, 1);
  assert.equal(claudeEntries[0].source, './plugins/codex-image');
  assert.equal(claudeEntries[0].version, '1.0.0');
  assert.equal(claudeEntries[0].category, 'productivity');

  assert.equal(codexEntries.length, 1);
  assert.deepEqual(codexEntries[0].source, { source: 'local', path: './plugins/codex-image' });
  assert.deepEqual(codexEntries[0].policy, {
    installation: 'AVAILABLE',
    authentication: 'ON_INSTALL',
  });
  assert.equal(codexEntries[0].category, 'Productivity');
});

test('ships every required file', () => {
  const requiredFiles = [
    'plugins/codex-image/README.md',
    'plugins/codex-image/commands/codex-image.md',
    'plugins/codex-image/lib/generate.mjs',
    'plugins/codex-image/lib/env.mjs',
    'plugins/codex-image/lib/config.mjs',
    'plugins/codex-image/lib/run.mjs',
    'plugins/codex-image/lib/validate-size.mjs',
    'plugins/codex-image/lib/safe-path.mjs',
    'plugins/codex-image/lib/parse-events.mjs',
    'plugins/codex-image/vendor/remove_chroma_key.py',
    'plugins/codex-image/vendor/LICENSE-imagegen.txt',
    'plugins/codex-image/skills/codex-image/SKILL.md',
    'plugins/codex-image/skills/codex-image/agents/openai.yaml',
    'plugins/codex-image/skills/codex-image/references/sizes.md',
    'plugins/codex-image/skills/codex-image/references/text-and-cjk.md',
    'plugins/codex-image/skills/codex-image/references/transparency.md',
    'plugins/codex-image/skills/codex-image/references/troubleshooting.md',
    'plugins/codex-image/skills/codex-image-setup/SKILL.md',
    'plugins/codex-image/skills/codex-image-setup/agents/openai.yaml',
    'plugins/codex-image/skills/codex-image-setup/assets/codex-image.local.md.template',
  ];

  for (const relativePath of requiredFiles) {
    assert.ok(read(relativePath).length > 0, `missing or empty: ${relativePath}`);
  }
});

test('vendors the chroma-key script under its Apache-2.0 license', () => {
  assert.match(read('plugins/codex-image/vendor/LICENSE-imagegen.txt'), /Apache License\n\s*Version 2\.0/);
  assert.match(read('plugins/codex-image/vendor/remove_chroma_key.py'), /chroma/i);
});

test('declares both skills with triggering frontmatter', () => {
  for (const [skill, expectedName] of [
    ['codex-image', 'codex-image'],
    ['codex-image-setup', 'codex-image-setup'],
  ]) {
    const metadata = frontmatter(read(`plugins/codex-image/skills/${skill}/SKILL.md`));
    assert.equal(metadata.name, expectedName);
    assert.ok(metadata.description && metadata.description.length > 40, `${skill} needs a trigger-rich description`);
  }
});

test('contains no local paths, host-specific hardcoding, or symlinks', () => {
  // The vendored upstream script is excluded (we do not rewrite third-party code).
  // tests/ is excluded from the /mnt/c/ rule only: the deny-list tests need it as
  // an adversarial fixture. Everything else is checked everywhere.
  const always = [
    [/\/home\/haha/, 'absolute developer home path'],
    [/gpt-5\.5|gpt-5\.6/, 'hardcoded dispatch model version'],
  ];

  for (const absolutePath of walkFiles(pluginRoot)) {
    if (absolutePath.includes(`${join('codex-image', 'vendor')}`)) continue;
    const content = readFileSync(absolutePath, 'utf8');
    for (const [pattern, why] of always) {
      assert.doesNotMatch(content, pattern, `${why} in ${absolutePath}`);
    }
    if (absolutePath.includes(`${join('codex-image', 'tests')}`)) continue;
    assert.doesNotMatch(content, /\/mnt\/c\//, `hardcoded Windows drive mount in ${absolutePath}`);
  }
});

test('never references ~/.codex without going through CODEX_HOME', () => {
  const skillRoot = join(pluginRoot, 'skills');
  for (const absolutePath of walkFiles(skillRoot)) {
    const content = readFileSync(absolutePath, 'utf8');
    for (const line of content.split('\n')) {
      if (!/~\/\.codex|\$HOME\/\.codex/.test(line)) continue;
      assert.ok(
        /CODEX_HOME/.test(line),
        `bare ~/.codex path must be expressed via CODEX_HOME: ${absolutePath}: ${line.trim()}`,
      );
    }
  }
});

test('keeps the anti code-drawing hard rules in the generation skill', () => {
  const skill = read('plugins/codex-image/skills/codex-image/SKILL.md');
  assert.match(skill, /MUST call the image_gen tool/);
  assert.match(skill, /MUST NOT write or execute any code that draws/);
  assert.match(skill, /AS-IS/);
  assert.match(skill, /code-drawing/i);
});

test('keeps the quality uncertainty honest and scoped to fallback-only controls', () => {
  const skill = read('plugins/codex-image/skills/codex-image/SKILL.md');
  assert.match(skill, /## Known uncertainty/);
  assert.match(skill, /fallback-only/);
  assert.match(skill, /Render at high quality if your image tool exposes a quality setting\./);
  assert.doesNotMatch(skill, /^\s*-\s*quality:\s*high\s*$/m, 'must not present quality as a built-in parameter');
});

test('never states token-count estimates that have no source', () => {
  const skill = read('plugins/codex-image/skills/codex-image/SKILL.md');
  assert.doesNotMatch(skill, /25[,.]?000|35[,.]?000|25k|35k/i);
});

test('text-and-cjk.md carries no pre-emptive CJK-blur disclaimer', () => {
  const reference = read('plugins/codex-image/skills/codex-image/references/text-and-cjk.md');

  // Regression guard: an earlier draft asserted, untested, that image-gen blurs
  // dense Chinese and that labels must be pre-emptively shortened.
  //
  // Quoted spans (「…」) are stripped first: the file legitimately quotes the
  // banned phrasing inside the rule that forbids it. We are testing for the
  // assertion, not for the string.
  const prose = reference.replace(/「[^」]*」/g, '');
  const disclaimers = [
    /CJK[^\n]{0,20}(可能有|會有)[^\n]{0,10}偏差/,
    /接受偏差/,
    /密集中文[^\n]{0,10}(會|可能)糊/,
    /標籤[^\n]{0,10}砍到/,
  ];
  for (const pattern of disclaimers) {
    assert.doesNotMatch(prose, pattern, 'must not pre-emptively assume CJK text will degrade');
  }

  assert.match(reference, /不預設中文會糊/);
  assert.match(reference, /正體中文/);
  assert.match(reference, /禁簡體/);

  // The file must NAME the pressure phrasing in order to forbid it, so a bare
  // absence check would be wrong. What must hold is that it is forbidden.
  assert.match(reference, /character for character/i, 'must name the pressure phrasing it forbids');
  assert.match(reference, /禁止[^\n]{0,20}(施壓|壓力)措辭/);
  assert.match(reference, /重生|重新生成/, 'typos are fixed by regenerating, never by code');
});

test('sizes.md scopes the four constraints to the CLI fallback path', () => {
  const reference = read('plugins/codex-image/skills/codex-image/references/sizes.md');
  assert.match(reference, /fallback CLI mode only/i);
  assert.match(reference, /advisory/i);
  assert.match(reference, /3840/);
  assert.match(reference, /待查|未查證|unverified/i);
});

test('setup skill gates feature enablement and old-skill removal on consent', () => {
  const setup = read('plugins/codex-image/skills/codex-image-setup/SKILL.md');

  const featureBlock = /image_generation[\s\S]{0,1200}/.exec(setup);
  assert.ok(featureBlock, 'setup must handle the image_generation feature flag');
  assert.match(featureBlock[0], /同意/, 'enabling the feature must require explicit consent');

  assert.match(setup, /unknown/, 'undetectable state must be recorded as unknown, not guessed');
  assert.match(setup, /不自行刪檔|不得自行刪檔/, 'old skill removal must never be unilateral');
  assert.doesNotMatch(setup, /全程唯讀/, 'the read-only claim is false and must not reappear');
  assert.doesNotMatch(setup, /只寫一個檔/, 'the single-write claim is false and must not reappear');
});

test('setup smoke test is opt-in and avoids code-drawing-inducing subjects', () => {
  const setup = read('plugins/codex-image/skills/codex-image-setup/SKILL.md');
  assert.match(setup, /opt-in|預設不跑/);
  assert.match(setup, /只驗通路/);
  assert.match(setup, /紅圓|紅色圓形/, 'must explain why a simple red circle is a bad smoke subject');
});

// The schema SSOT is lib/config.mjs. Parse the field lists out of it rather than
// restating them, so the template can never silently drift from the code.
function schemaFields() {
  const source = read('plugins/codex-image/lib/config.mjs');
  return ['DETECTED_FIELDS', 'PREFERENCE_FIELDS', 'META_FIELDS'].flatMap((name) => {
    const match = new RegExp(`export const ${name} = \\[([\\s\\S]*?)\\];`).exec(source);
    assert.ok(match, `lib/config.mjs must export ${name}`);
    return [...match[1].matchAll(/'([a-z0-9_]+)'/g)].map((m) => m[1]);
  });
}

test('config template ships every schema field and the user-notes markers', () => {
  const template = read('plugins/codex-image/skills/codex-image-setup/assets/codex-image.local.md.template');
  const fields = schemaFields();
  assert.ok(fields.length >= 24, 'schema parse looks wrong');
  for (const field of fields) {
    assert.match(template, new RegExp(`^${field}:`, 'm'), `template missing field: ${field}`);
  }
  assert.match(template, /<!-- codex-image:user-notes:begin -->/);
  assert.match(template, /<!-- codex-image:user-notes:end -->/);

  // parseConfig() only decodes array values that start with '[', so a YAML
  // block list here would silently fail to round-trip.
  assert.match(template, /^deny_write_paths: \[[^\]]*\]/m, 'deny_write_paths must be an inline JSON array');
  assert.doesNotMatch(template, /\/mnt\/c/, 'the default deny entry is /mnt/, not a specific drive');
});

test('setup skill and README describe the same schema as lib/config.mjs', () => {
  const fields = schemaFields();
  const docs = [
    'plugins/codex-image/skills/codex-image-setup/SKILL.md',
    'plugins/codex-image/README.md',
  ];

  for (const doc of docs) {
    const content = read(doc);
    for (const field of fields) {
      assert.match(content, new RegExp(`\`${field}\``), `${doc} does not document field: ${field}`);
    }
  }

  // Names retired during review must not linger anywhere in the plugin docs.
  const retired = [
    'codex_version', 'detected_effort', 'python_command',
    'openai_key_present', 'python_available', 'transparency_available',
  ];
  for (const doc of [...docs, 'plugins/codex-image/skills/codex-image/SKILL.md',
    'plugins/codex-image/skills/codex-image-setup/assets/codex-image.local.md.template']) {
    const content = read(doc);
    for (const name of retired) {
      assert.doesNotMatch(content, new RegExp(`\`${name}\`|^${name}:`, 'm'), `${doc} uses retired field name: ${name}`);
    }
  }
});

test('documents the plugin in the repository README', () => {
  const readme = read('README.md');
  const pluginCount = readJson('.agents/plugins/marketplace.json').plugins.length;
  assert.match(readme, /\*\*codex-image\*\*/);
  assert.match(readme, new RegExp(`Codex marketplace 目前列出 ${pluginCount} 個 plugins`));
});

test('plugin README discloses the known risks', () => {
  const readme = read('plugins/codex-image/README.md');
  assert.match(readme, /heuristic|啟發式/i);
  assert.match(readme, /symlink/i);
  assert.match(readme, /pillow/i);
  assert.match(readme, /Windows/);
  assert.match(readme, /CLAUDE_PLUGIN_ROOT/);
});

test('lib modules are ESM with named exports only', () => {
  const libRoot = join(pluginRoot, 'lib');
  assert.ok(existsSync(libRoot));
  for (const absolutePath of walkFiles(libRoot)) {
    assert.match(absolutePath, /\.mjs$/, `lib must be ESM .mjs: ${absolutePath}`);
    const content = readFileSync(absolutePath, 'utf8');
    assert.doesNotMatch(content, /export\s+default/, `named exports only: ${absolutePath}`);
    assert.doesNotMatch(content, /console\.log\(/, `no debug logging: ${absolutePath}`);
  }
});
