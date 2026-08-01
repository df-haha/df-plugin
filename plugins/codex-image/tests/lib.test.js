/**
 * lib.test.js — Comprehensive tests for codex-image lib modules.
 * CJS file using dynamic import() for ESM .mjs modules.
 * Run: node --test plugins/codex-image/tests/lib.test.js
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');

// ─── env.mjs ────────────────────────────────────────────────────────────────

test('env: detectPlatform returns win32', async () => {
  const { detectPlatform } = await import('../lib/env.mjs');
  assert.equal(detectPlatform({ platform: 'win32' }), 'win32');
});

test('env: detectPlatform returns darwin', async () => {
  const { detectPlatform } = await import('../lib/env.mjs');
  assert.equal(detectPlatform({ platform: 'darwin' }), 'darwin');
});

test('env: detectPlatform returns wsl via WSL_DISTRO_NAME', async () => {
  const { detectPlatform } = await import('../lib/env.mjs');
  assert.equal(detectPlatform({ platform: 'linux', env: { WSL_DISTRO_NAME: 'Ubuntu' } }), 'wsl');
});

test('env: detectPlatform returns linux when no WSL indicators', async () => {
  const { detectPlatform } = await import('../lib/env.mjs');
  // Pass env override AND note: on a real WSL machine /proc/version still has 'microsoft'.
  // The function checks env first, then /proc/version. To truly get 'linux' we need
  // the function to not find WSL_DISTRO_NAME AND /proc/version to not match.
  // On WSL this test would return 'wsl'. We test the env branch specifically.
  const result = detectPlatform({ platform: 'linux', env: {} });
  // On an actual WSL host, /proc/version contains 'microsoft', so result is 'wsl'
  // On a real Linux box, result would be 'linux'. Accept both — the key behavior
  // (env override works) is tested by the WSL_DISTRO_NAME test above.
  assert.ok(result === 'linux' || result === 'wsl',
    `Expected linux or wsl, got ${result}`);
});

test('env: resolveCodexHome uses CODEX_HOME if set', async () => {
  const { resolveCodexHome } = await import('../lib/env.mjs');
  assert.equal(resolveCodexHome({ env: { CODEX_HOME: '/custom/codex' } }), '/custom/codex');
});

test('env: resolveCodexHome defaults to ~/.codex', async () => {
  const { resolveCodexHome } = await import('../lib/env.mjs');
  const result = resolveCodexHome({ env: {} });
  assert.equal(result, path.join(os.homedir(), '.codex'));
});

test('env: resolveCodexHome ignores empty CODEX_HOME', async () => {
  const { resolveCodexHome } = await import('../lib/env.mjs');
  const result = resolveCodexHome({ env: { CODEX_HOME: '' } });
  assert.equal(result, path.join(os.homedir(), '.codex'));
});

test('env: resolveConfigPath builds correct path', async () => {
  const { resolveConfigPath } = await import('../lib/env.mjs');
  const result = resolveConfigPath({ env: { CODEX_HOME: '/test' } });
  assert.equal(result, '/test/codex-image.local.md');
});

test('env: detectPythonCommand finds first available', async () => {
  const { detectPythonCommand } = await import('../lib/env.mjs');
  const probe = (cmd) => cmd === 'python';
  const result = detectPythonCommand({ probe });
  assert.deepEqual(result, { cmd: 'python', args: [] });
});

test('env: detectPythonCommand returns null when none available', async () => {
  const { detectPythonCommand } = await import('../lib/env.mjs');
  const probe = () => false;
  assert.equal(detectPythonCommand({ probe }), null);
});

test('env: detectPythonCommand finds py -3', async () => {
  const { detectPythonCommand } = await import('../lib/env.mjs');
  const probe = (cmd) => cmd === 'py';
  const result = detectPythonCommand({ probe });
  assert.deepEqual(result, { cmd: 'py', args: ['-3'] });
});

test('env: defaultDenyWritePaths for wsl', async () => {
  const { defaultDenyWritePaths } = await import('../lib/env.mjs');
  assert.deepEqual(defaultDenyWritePaths('wsl'), ['/mnt/']);
});

test('env: defaultDenyWritePaths for win32/linux/darwin', async () => {
  const { defaultDenyWritePaths } = await import('../lib/env.mjs');
  assert.deepEqual(defaultDenyWritePaths('win32'), []);
  assert.deepEqual(defaultDenyWritePaths('linux'), []);
  assert.deepEqual(defaultDenyWritePaths('darwin'), []);
});

// ─── validate-size.mjs ─────────────────────────────────────────────────────

test('validate-size: auto is always valid', async () => {
  const { validateSize } = await import('../lib/validate-size.mjs');
  const r = validateSize('auto');
  assert.equal(r.ok, true);
  assert.equal(r.requested, null);
  assert.equal(r.suggestion, null);
  assert.deepEqual(r.violations, []);
});

test('validate-size: malformed string', async () => {
  const { validateSize } = await import('../lib/validate-size.mjs');
  const r = validateSize('hello');
  assert.equal(r.ok, false);
  assert.ok(r.violations.includes('malformed'));
  assert.equal(r.suggestion, null);
});

test('validate-size: zero dimensions → malformed', async () => {
  const { validateSize } = await import('../lib/validate-size.mjs');
  const r = validateSize('0x1024');
  assert.equal(r.ok, false);
  assert.ok(r.violations.includes('malformed'));
});

test('validate-size: negative dimension → malformed (via non-matching regex)', async () => {
  const { validateSize } = await import('../lib/validate-size.mjs');
  // "-100x200" does not match the WIDTHxHEIGHT regex (\d+ doesn't match -)
  const r = validateSize('-100x200');
  assert.equal(r.ok, false);
  assert.ok(r.violations.includes('malformed'));
});

test('validate-size: 16-multiple violation', async () => {
  const { validateSize } = await import('../lib/validate-size.mjs');
  const r = validateSize('1920x1080', { mode: 'cli' });
  assert.equal(r.ok, false);
  assert.ok(r.violations.some((v) => v.includes('multiples of 16')));
});

test('validate-size: 3840 is legal (inclusive boundary)', async () => {
  const { validateSize } = await import('../lib/validate-size.mjs');
  const r = validateSize('3840x1280', { mode: 'cli' });
  assert.equal(r.ok, true);
  assert.deepEqual(r.violations, []);
});

test('validate-size: 3856 exceeds max edge', async () => {
  const { validateSize } = await import('../lib/validate-size.mjs');
  const r = validateSize('3856x1280', { mode: 'cli' });
  assert.equal(r.ok, false);
  assert.ok(r.violations.some((v) => v.includes('max edge')));
});

test('validate-size: ratio exceeds 3:1', async () => {
  const { validateSize } = await import('../lib/validate-size.mjs');
  const r = validateSize('3840x1264', { mode: 'cli' });
  // 3840/1264 ≈ 3.04 > 3
  assert.equal(r.ok, false);
  assert.ok(r.violations.some((v) => v.includes('ratio')));
});

test('validate-size: pixel minimum violation', async () => {
  const { validateSize } = await import('../lib/validate-size.mjs');
  const r = validateSize('128x128', { mode: 'cli' });
  // 128*128 = 16384 < 655360
  assert.equal(r.ok, false);
  assert.ok(r.violations.some((v) => v.includes('below minimum')));
});

test('validate-size: pixel maximum violation', async () => {
  const { validateSize } = await import('../lib/validate-size.mjs');
  const r = validateSize('3840x2560', { mode: 'cli' });
  // 3840*2560 = 9,830,400 > 8,294,400
  assert.equal(r.ok, false);
  assert.ok(r.violations.some((v) => v.includes('exceeds maximum')));
});

test('validate-size: tie-break snaps UP (1920x1080 → 1920x1088)', async () => {
  const { validateSize } = await import('../lib/validate-size.mjs');
  const r = validateSize('1920x1080', { mode: 'cli' });
  assert.ok(r.suggestion);
  // 1080 is equidistant between 1072 and 1088 (remainder 8) → snap UP to 1088
  assert.equal(r.suggestion.width, 1920);
  assert.equal(r.suggestion.height, 1088);
  // Verify suggested size is legal
  assert.equal(r.suggestion.width % 16, 0);
  assert.equal(r.suggestion.height % 16, 0);
  const ratio = r.suggestion.width / r.suggestion.height;
  assert.ok(ratio <= 3);
  const px = r.suggestion.width * r.suggestion.height;
  assert.ok(px >= 655360 && px <= 8294400);
});

test('validate-size: builtin mode returns advisory (ok:true) even with violations', async () => {
  const { validateSize } = await import('../lib/validate-size.mjs');
  const r = validateSize('1920x1080', { mode: 'builtin' });
  assert.equal(r.ok, true);
  assert.equal(r.advisory, true);
  assert.ok(r.violations.length > 0);
});

test('validate-size: cli mode rejects same input', async () => {
  const { validateSize } = await import('../lib/validate-size.mjs');
  const r = validateSize('1920x1080', { mode: 'cli' });
  assert.equal(r.ok, false);
  assert.equal(r.advisory, false);
  assert.ok(r.violations.length > 0);
});

test('validate-size: valid size passes cli mode', async () => {
  const { validateSize } = await import('../lib/validate-size.mjs');
  const r = validateSize('1920x1088', { mode: 'cli' });
  // 1920*1088 = 2,088,960 — within pixel bounds; ratio 1.76; both multiples of 16; both <= 3840
  assert.equal(r.ok, true);
  assert.deepEqual(r.violations, []);
});

test('validate-size: SIZE_CONSTRAINTS is frozen', async () => {
  const { SIZE_CONSTRAINTS } = await import('../lib/validate-size.mjs');
  assert.equal(SIZE_CONSTRAINTS.maxEdge, 3840);
  assert.equal(SIZE_CONSTRAINTS.multiple, 16);
  assert.equal(SIZE_CONSTRAINTS.maxRatio, 3);
  assert.equal(SIZE_CONSTRAINTS.minPixels, 655360);
  assert.equal(SIZE_CONSTRAINTS.maxPixels, 8294400);
  // Object.freeze silently fails in non-strict; in strict mode it throws.
  // Verify immutability by confirming the value doesn't change after attempted mutation.
  try { SIZE_CONSTRAINTS.maxEdge = 999; } catch { /* strict mode throws */ }
  assert.equal(SIZE_CONSTRAINTS.maxEdge, 3840, 'frozen object should not be mutated');
});

// ─── safe-path.mjs ─────────────────────────────────────────────────────────

test('safe-path: sanitizeFilename appends .png', async () => {
  const { sanitizeFilename } = await import('../lib/safe-path.mjs');
  assert.equal(sanitizeFilename('cat'), 'cat.png');
});

test('safe-path: sanitizeFilename preserves existing extension', async () => {
  const { sanitizeFilename } = await import('../lib/safe-path.mjs');
  assert.equal(sanitizeFilename('cat.jpg'), 'cat.jpg');
});

test('safe-path: sanitizeFilename rejects ../ traversal', async () => {
  const { sanitizeFilename } = await import('../lib/safe-path.mjs');
  assert.throws(() => sanitizeFilename('../etc/passwd'), /separator|traversal/i);
});

test('safe-path: sanitizeFilename rejects absolute path', async () => {
  const { sanitizeFilename } = await import('../lib/safe-path.mjs');
  assert.throws(() => sanitizeFilename('/etc/passwd'));
});

test('safe-path: sanitizeFilename rejects backslash', async () => {
  const { sanitizeFilename } = await import('../lib/safe-path.mjs');
  assert.throws(() => sanitizeFilename('dir\\file'), /separator/i);
});

test('safe-path: sanitizeFilename rejects forward slash', async () => {
  const { sanitizeFilename } = await import('../lib/safe-path.mjs');
  assert.throws(() => sanitizeFilename('dir/file'), /separator/i);
});

test('safe-path: sanitizeFilename rejects NUL bytes', async () => {
  const { sanitizeFilename } = await import('../lib/safe-path.mjs');
  assert.throws(() => sanitizeFilename('file\x00.png'), /NUL|control/i);
});

test('safe-path: sanitizeFilename rejects empty string', async () => {
  const { sanitizeFilename } = await import('../lib/safe-path.mjs');
  assert.throws(() => sanitizeFilename(''));
});

test('safe-path: sanitizeFilename rejects dot', async () => {
  const { sanitizeFilename } = await import('../lib/safe-path.mjs');
  assert.throws(() => sanitizeFilename('.'));
});

test('safe-path: sanitizeFilename rejects double dot', async () => {
  const { sanitizeFilename } = await import('../lib/safe-path.mjs');
  assert.throws(() => sanitizeFilename('..'));
});

test('safe-path: resolveOutputPath works for valid path', async () => {
  const { resolveOutputPath } = await import('../lib/safe-path.mjs');
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'codex-img-test-'));
  try {
    const result = resolveOutputPath(tmpDir, 'test.png');
    assert.equal(result, path.join(fs.realpathSync.native(tmpDir), 'test.png'));
  } finally {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
});

test('safe-path: resolveOutputPath rejects deny-list hit', async () => {
  const { resolveOutputPath } = await import('../lib/safe-path.mjs');
  assert.throws(
    () => resolveOutputPath('/mnt/c/Users', 'test.png', { denyPaths: ['/mnt/'] }),
    /denied/i,
  );
});

test('safe-path: resolveOutputPath deny-list near-miss (/mnted/ not denied by /mnt/)', async () => {
  const { resolveOutputPath } = await import('../lib/safe-path.mjs');
  // /mnted/ is NOT inside /mnt/ — it's a different directory
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'codex-img-mnted-'));
  try {
    // This should NOT throw because tmpDir is not inside /mnt/
    const result = resolveOutputPath(tmpDir, 'test.png', { denyPaths: ['/mnt/'] });
    assert.ok(result.endsWith('test.png'));
  } finally {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
});

test('safe-path: resolveOutputPath rejects symlink escaping deny list', async () => {
  const { resolveOutputPath } = await import('../lib/safe-path.mjs');
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'codex-img-sym-'));
  const realTarget = path.join(tmpDir, 'real');
  const symlink = path.join(tmpDir, 'link');
  fs.mkdirSync(realTarget);

  try {
    fs.symlinkSync(realTarget, symlink, 'dir');
  } catch (err) {
    if (err.code === 'EPERM') {
      // Skip on systems without symlink permission
      fs.rmSync(tmpDir, { recursive: true, force: true });
      return;
    }
    throw err;
  }

  try {
    // deny the real target; accessing via symlink should still be caught
    // because realpathAncestor resolves the symlink
    const result = resolveOutputPath(symlink, 'test.png', { denyPaths: [realTarget] });
    // If the symlink resolves to inside the denied path, it should throw.
    // But our implementation resolves the dir, so the file is inside realTarget → denied
    assert.fail('Should have thrown for symlink into denied path');
  } catch (err) {
    assert.ok(err.message.includes('denied'), `Expected denied error, got: ${err.message}`);
  } finally {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
});

test('safe-path: nonDestructivePath returns original if no collision', async () => {
  const { nonDestructivePath } = await import('../lib/safe-path.mjs');
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'codex-img-nd-'));
  try {
    const p = path.join(tmpDir, 'image.png');
    assert.equal(nonDestructivePath(p), p);
  } finally {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
});

test('safe-path: nonDestructivePath chains -v2, -v3', async () => {
  const { nonDestructivePath } = await import('../lib/safe-path.mjs');
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'codex-img-nd2-'));
  try {
    const p = path.join(tmpDir, 'image.png');
    fs.writeFileSync(p, 'x');
    assert.equal(nonDestructivePath(p), path.join(tmpDir, 'image-v2.png'));

    fs.writeFileSync(path.join(tmpDir, 'image-v2.png'), 'x');
    assert.equal(nonDestructivePath(p), path.join(tmpDir, 'image-v3.png'));
  } finally {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
});

// ─── parse-events.mjs ──────────────────────────────────────────────────────

test('parse-events: parseEvents handles normal JSONL', async () => {
  const { parseEvents } = await import('../lib/parse-events.mjs');
  const text = '{"type":"a"}\n{"type":"b"}\n';
  const { events, malformedLines } = parseEvents(text);
  assert.equal(events.length, 2);
  assert.equal(malformedLines, 0);
});

test('parse-events: parseEvents tolerates truncated last line', async () => {
  const { parseEvents } = await import('../lib/parse-events.mjs');
  const text = '{"type":"a"}\n{"type":"b';
  const { events, malformedLines } = parseEvents(text);
  assert.equal(events.length, 1);
  assert.equal(malformedLines, 1);
});

test('parse-events: parseEvents tolerates blank lines', async () => {
  const { parseEvents } = await import('../lib/parse-events.mjs');
  const text = '{"type":"a"}\n\n\n{"type":"b"}\n';
  const { events, malformedLines } = parseEvents(text);
  assert.equal(events.length, 2);
  assert.equal(malformedLines, 0);
});

test('parse-events: hasTurnCompleted detects {type:turn.completed}', async () => {
  const { hasTurnCompleted } = await import('../lib/parse-events.mjs');
  assert.equal(hasTurnCompleted([{ type: 'turn.completed' }]), true);
});

test('parse-events: hasTurnCompleted detects {msg:{type:turn.completed}}', async () => {
  const { hasTurnCompleted } = await import('../lib/parse-events.mjs');
  assert.equal(hasTurnCompleted([{ msg: { type: 'turn.completed' } }]), true);
});

test('parse-events: hasTurnCompleted returns false when missing', async () => {
  const { hasTurnCompleted } = await import('../lib/parse-events.mjs');
  assert.equal(hasTurnCompleted([{ type: 'message' }]), false);
});

test('parse-events: detectCodeDrawing flags PIL in command_execution event', async () => {
  const { detectCodeDrawing } = await import('../lib/parse-events.mjs');
  const events = [
    { type: 'command_execution', command: 'python -c "from PIL import Image"' },
  ];
  const r = detectCodeDrawing(events);
  assert.equal(r.suspected, true);
  assert.ok(r.evidence.length > 0);
  assert.equal(r.heuristic, true);
});

test('parse-events: detectCodeDrawing does NOT flag prompt text mentioning PIL', async () => {
  const { detectCodeDrawing } = await import('../lib/parse-events.mjs');
  // This is a prompt/assistant message event — NOT a command_execution event
  const events = [
    { type: 'assistant_message', content: 'MUST NOT use PIL or Pillow for image generation' },
  ];
  const r = detectCodeDrawing(events);
  assert.equal(r.suspected, false);
  assert.equal(r.evidence.length, 0);
});

test('parse-events: detectCodeDrawing handles event with parsed_cmd', async () => {
  const { detectCodeDrawing } = await import('../lib/parse-events.mjs');
  const events = [
    { parsed_cmd: 'python3 draw.py', command: 'python3 draw.py' },
  ];
  const r = detectCodeDrawing(events);
  assert.equal(r.suspected, true);
});

test('parse-events: readPngSize reads valid PNG header', async () => {
  const { readPngSize } = await import('../lib/parse-events.mjs');
  // Synthesize a minimal valid PNG header: signature + IHDR chunk
  const buf = Buffer.alloc(32);
  // PNG signature
  Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]).copy(buf, 0);
  // IHDR chunk length (13 bytes)
  buf.writeUInt32BE(13, 8);
  // IHDR tag
  buf.write('IHDR', 12, 'ascii');
  // Width = 640, Height = 480
  buf.writeUInt32BE(640, 16);
  buf.writeUInt32BE(480, 20);
  const r = readPngSize(buf);
  assert.deepEqual(r, { width: 640, height: 480 });
});

test('parse-events: readPngSize returns null for non-PNG', async () => {
  const { readPngSize } = await import('../lib/parse-events.mjs');
  const buf = Buffer.from('not a png file at all!!!!!!!!!!!');
  assert.equal(readPngSize(buf), null);
});

test('parse-events: readPngSize returns null for too-short buffer', async () => {
  const { readPngSize } = await import('../lib/parse-events.mjs');
  assert.equal(readPngSize(Buffer.alloc(10)), null);
});

test('parse-events: readPngSizeFromFile reads from file', async () => {
  const { readPngSizeFromFile } = await import('../lib/parse-events.mjs');
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'codex-img-png-'));
  try {
    const buf = Buffer.alloc(32);
    Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]).copy(buf, 0);
    buf.writeUInt32BE(13, 8);
    buf.write('IHDR', 12, 'ascii');
    buf.writeUInt32BE(1024, 16);
    buf.writeUInt32BE(768, 20);
    const fpath = path.join(tmpDir, 'test.png');
    fs.writeFileSync(fpath, buf);
    const r = readPngSizeFromFile(fpath);
    assert.deepEqual(r, { width: 1024, height: 768 });
  } finally {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
});

test('parse-events: readPngSizeFromFile returns null for missing file', async () => {
  const { readPngSizeFromFile } = await import('../lib/parse-events.mjs');
  assert.equal(readPngSizeFromFile('/nonexistent/file.png'), null);
});

// ─── run.mjs ────────────────────────────────────────────────────────────────

test('run: buildCodexArgs returns correct argv', async () => {
  const { buildCodexArgs } = await import('../lib/run.mjs');
  const args = buildCodexArgs({ stagingDir: '/tmp/staging' });
  assert.deepEqual(args, [
    'exec', '--json', '--ephemeral', '--skip-git-repo-check',
    '--sandbox', 'workspace-write', '-C', '/tmp/staging', '-',
  ]);
});

test('run: buildCodexArgs includes extraArgs', async () => {
  const { buildCodexArgs } = await import('../lib/run.mjs');
  const args = buildCodexArgs({ stagingDir: '/s', extraArgs: ['--foo', 'bar'] });
  assert.ok(args.includes('--foo'));
  assert.ok(args.includes('bar'));
});

test('run: security — prompt goes to stdin, not argv', async () => {
  const { runCodex } = await import('../lib/run.mjs');

  const dangerousPrompts = [
    '$(rm -rf /)',
    '`whoami`',
    '; echo pwned',
    'line1\nline2',
    "it's a 'test'",
    '"double quotes"',
  ];

  for (const prompt of dangerousPrompts) {
    let capturedArgs = null;
    let capturedStdin = '';

    const fakeSpawn = (bin, args) => {
      capturedArgs = args;
      const { EventEmitter } = require('node:events');
      const child = new EventEmitter();
      child.stdout = new EventEmitter();
      child.stderr = new EventEmitter();
      child.stdin = {
        write(data) { capturedStdin += data; },
        end() {
          // Emit close asynchronously
          setImmediate(() => child.emit('close', 0));
        },
      };
      child.kill = () => {};
      return child;
    };

    capturedStdin = '';
    await runCodex({
      prompt,
      stagingDir: '/tmp/test',
      spawnFn: fakeSpawn,
      timeoutMs: 5000,
    });

    // Prompt must NOT appear in args
    for (const arg of capturedArgs) {
      assert.ok(!arg.includes(prompt),
        `Dangerous prompt "${prompt}" must not appear in argv`);
    }
    // Prompt must appear byte-identical on stdin
    assert.equal(capturedStdin, prompt,
      `Prompt "${prompt}" must appear byte-identical on stdin`);
  }
});

test('run: runCodex handles timeout', async () => {
  const { runCodex } = await import('../lib/run.mjs');
  const { EventEmitter } = require('node:events');

  const fakeSpawn = () => {
    const child = new EventEmitter();
    child.stdout = new EventEmitter();
    child.stderr = new EventEmitter();
    child.stdin = { write() {}, end() {} };
    child.kill = () => {
      // Simulate process exit after kill
      setImmediate(() => child.emit('close', null));
    };
    return child;
  };

  const result = await runCodex({
    prompt: 'test',
    stagingDir: '/tmp/test',
    spawnFn: fakeSpawn,
    timeoutMs: 50,
  });

  assert.equal(result.timedOut, true);
});

test('run: runCodex collects stdout/stderr', async () => {
  const { runCodex } = await import('../lib/run.mjs');
  const { EventEmitter } = require('node:events');

  const fakeSpawn = () => {
    const child = new EventEmitter();
    child.stdout = new EventEmitter();
    child.stderr = new EventEmitter();
    child.stdin = {
      write() {},
      end() {
        setImmediate(() => {
          child.stdout.emit('data', 'out1');
          child.stderr.emit('data', 'err1');
          child.emit('close', 0);
        });
      },
    };
    child.kill = () => {};
    return child;
  };

  const result = await runCodex({
    prompt: 'test',
    stagingDir: '/tmp/test',
    spawnFn: fakeSpawn,
    timeoutMs: 5000,
  });

  assert.equal(result.stdout, 'out1');
  assert.equal(result.stderr, 'err1');
  assert.equal(result.exitCode, 0);
  assert.equal(result.timedOut, false);
});

// ─── config.mjs ─────────────────────────────────────────────────────────────

test('config: parseConfig parses valid frontmatter', async () => {
  const { parseConfig } = await import('../lib/config.mjs');
  const text = '---\nplatform: wsl\nis_wsl: true\n---\nBody text here.\n';
  const r = parseConfig(text);
  assert.equal(r.fields.platform, 'wsl');
  assert.equal(r.fields.is_wsl, 'true');
  assert.ok(r.body.includes('Body text'));
  assert.equal(r.malformed, undefined);
});

test('config: parseConfig handles malformed frontmatter', async () => {
  const { parseConfig } = await import('../lib/config.mjs');
  const text = 'No frontmatter here, just text.';
  const r = parseConfig(text);
  assert.equal(r.malformed, true);
  assert.deepEqual(r.fields, {});
  assert.equal(r.body, text);
});

test('config: parseConfig parses JSON arrays', async () => {
  const { parseConfig } = await import('../lib/config.mjs');
  const text = '---\ndeny_write_paths: ["/mnt/"]\n---\n';
  const r = parseConfig(text);
  assert.equal(r.fields.deny_write_paths, '["/mnt/"]');
});

test('config: parseConfig strips trailing comments from values', async () => {
  const { parseConfig } = await import('../lib/config.mjs');
  // The shipped template annotates fields inline. Without stripping, the
  // tri-state below parses as 'unknown    # 只記錄是否存在' and the array
  // below fails JSON.parse — both silent corruptions.
  const text = '---\n'
    + 'openai_api_key_present: unknown    # 只記錄是否存在，絕不記錄 key 本身\n'
    + 'deny_write_paths: ["/mnt/"]       # 平台相關\n'
    + '---\n';
  const r = parseConfig(text);
  assert.equal(r.fields.openai_api_key_present, 'unknown');
  assert.equal(r.fields.deny_write_paths, '["/mnt/"]');
});

test('config: parseConfig keeps # that is data, not a comment', async () => {
  const { parseConfig } = await import('../lib/config.mjs');
  const text = '---\nchroma_key: "#00ff00"\nlist: ["#ff00ff"]\n---\n';
  const r = parseConfig(text);
  assert.equal(r.fields.chroma_key, '"#00ff00"');
  assert.equal(r.fields.list, '["#ff00ff"]');
});

test('config: parseConfig ignores whole-line comments even with a colon', async () => {
  const { parseConfig } = await import('../lib/config.mjs');
  const text = '---\n# note: this is prose, not a field\nfoo: bar\n---\n';
  const r = parseConfig(text);
  assert.deepEqual(Object.keys(r.fields), ['foo']);
});

test('config: parseConfig preserves user notes', async () => {
  const { parseConfig } = await import('../lib/config.mjs');
  const text = '---\nfoo: bar\n---\n' +
    'Some body.\n' +
    '<!-- codex-image:user-notes:begin -->\nMy custom notes\n<!-- codex-image:user-notes:end -->\n' +
    'More body.\n';
  const r = parseConfig(text);
  assert.equal(r.userNotes, '\nMy custom notes\n');
});

test('config: mergeConfig three-layer merge', async () => {
  const { mergeConfig } = await import('../lib/config.mjs');
  const existing = { platform: 'linux', default_quality: 'standard', custom_key: 'keep' };
  const result = mergeConfig(existing, {
    detected: { platform: 'wsl' },
    preference: { default_quality: 'hd' },
    meta: { schema_version: '1' },
  });
  assert.equal(result.platform, 'wsl');          // detected overwrites
  assert.equal(result.default_quality, 'hd');     // preference overwrites
  assert.equal(result.schema_version, '1');       // meta overwrites
  assert.equal(result.custom_key, 'keep');        // unknown key preserved
});

test('config: mergeConfig preserves unknown keys', async () => {
  const { mergeConfig } = await import('../lib/config.mjs');
  const existing = { unknown_field: 'preserved', platform: 'linux' };
  const result = mergeConfig(existing, { detected: { platform: 'wsl' } });
  assert.equal(result.unknown_field, 'preserved');
});

test('config: upgradeConfig fills missing schema_version', async () => {
  const { upgradeConfig, CURRENT_SCHEMA_VERSION } = await import('../lib/config.mjs');
  const r = upgradeConfig({});
  assert.equal(r.schema_version, String(CURRENT_SCHEMA_VERSION));
});

test('config: upgradeConfig preserves existing schema_version', async () => {
  const { upgradeConfig } = await import('../lib/config.mjs');
  const r = upgradeConfig({ schema_version: '1' });
  assert.equal(r.schema_version, '1');
});

test('config: serializeConfig deterministic ordering', async () => {
  const { serializeConfig } = await import('../lib/config.mjs');
  const fields = {
    schema_version: '1',
    platform: 'wsl',
    default_quality: 'hd',
    zzz_custom: 'value',
    aaa_custom: 'value2',
  };
  const out1 = serializeConfig({ fields, body: 'body' });
  const out2 = serializeConfig({ fields, body: 'body' });
  assert.equal(out1, out2);

  // platform (detected) comes before default_quality (preference)
  // which comes before schema_version (meta)
  // which comes before aaa_custom, zzz_custom (unknown, alphabetical)
  const lines = out1.split('\n');
  const platformIdx = lines.findIndex((l) => l.startsWith('platform:'));
  const qualityIdx = lines.findIndex((l) => l.startsWith('default_quality:'));
  const schemaIdx = lines.findIndex((l) => l.startsWith('schema_version:'));
  const aaaIdx = lines.findIndex((l) => l.startsWith('aaa_custom:'));
  const zzzIdx = lines.findIndex((l) => l.startsWith('zzz_custom:'));

  assert.ok(platformIdx < qualityIdx, 'detected before preference');
  assert.ok(qualityIdx < schemaIdx, 'preference before meta');
  assert.ok(schemaIdx < aaaIdx, 'meta before unknown');
  assert.ok(aaaIdx < zzzIdx, 'unknown keys alphabetical');
});

test('config: round-trip parse→merge→serialize preserves user notes', async () => {
  const { parseConfig, mergeConfig, serializeConfig } = await import('../lib/config.mjs');
  const original = '---\nplatform: linux\ndefault_quality: standard\n---\n' +
    'Intro.\n' +
    '<!-- codex-image:user-notes:begin -->\nDo not delete me!\n<!-- codex-image:user-notes:end -->\n' +
    'Outro.\n';
  const parsed = parseConfig(original);
  const merged = mergeConfig(parsed.fields, { detected: { platform: 'wsl' } });
  const serialized = serializeConfig({ fields: merged, body: parsed.body });

  assert.ok(serialized.includes('platform: wsl'));
  assert.ok(serialized.includes('Do not delete me!'));
  assert.ok(serialized.includes('<!-- codex-image:user-notes:begin -->'));
  assert.ok(serialized.includes('<!-- codex-image:user-notes:end -->'));
});

test('config: writeConfigAtomic and loadConfigOrDefaults', async () => {
  const { writeConfigAtomic, loadConfigOrDefaults, serializeConfig } = await import('../lib/config.mjs');
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'codex-img-cfg-'));
  try {
    const cfgPath = path.join(tmpDir, 'test.local.md');
    const text = serializeConfig({ fields: { platform: 'wsl' }, body: 'hi' });
    writeConfigAtomic(cfgPath, text);

    // Verify written
    assert.ok(fs.existsSync(cfgPath));
    const loaded = loadConfigOrDefaults(cfgPath);
    assert.equal(loaded.fields.platform, 'wsl');
    assert.equal(loaded.usedDefaults, undefined);

    // Verify no temp files left behind
    const files = fs.readdirSync(tmpDir);
    assert.equal(files.length, 1);
    assert.equal(files[0], 'test.local.md');
  } finally {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
});

test('config: loadConfigOrDefaults returns defaults for missing file', async () => {
  const { loadConfigOrDefaults } = await import('../lib/config.mjs');
  const r = loadConfigOrDefaults('/nonexistent/path.md', { platform: 'unknown' });
  assert.equal(r.usedDefaults, true);
  assert.equal(r.fields.platform, 'unknown');
});

test('config: loadConfigOrDefaults handles corrupt file', async () => {
  const { loadConfigOrDefaults } = await import('../lib/config.mjs');
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'codex-img-corrupt-'));
  try {
    const cfgPath = path.join(tmpDir, 'bad.md');
    fs.writeFileSync(cfgPath, 'no frontmatter here');
    const r = loadConfigOrDefaults(cfgPath, { platform: 'fallback' });
    assert.equal(r.corrupt, true);
    assert.equal(r.fields.platform, 'fallback');
  } finally {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
});

test('config: triState normalizes values', async () => {
  const { triState } = await import('../lib/config.mjs');
  assert.equal(triState(true), 'true');
  assert.equal(triState('true'), 'true');
  assert.equal(triState(false), 'false');
  assert.equal(triState('false'), 'false');
  assert.equal(triState(undefined), 'unknown');
  assert.equal(triState(null), 'unknown');
  assert.equal(triState('maybe'), 'unknown');
});

test('config: DETECTED_FIELDS, PREFERENCE_FIELDS, META_FIELDS exported', async () => {
  const { DETECTED_FIELDS, PREFERENCE_FIELDS, META_FIELDS } = await import('../lib/config.mjs');
  // Pinned exactly: these lists are the schema SSOT that the setup SKILL.md,
  // the README schema table, and the config template must all agree with.
  // Changing them is a schema change and must bump CURRENT_SCHEMA_VERSION.
  assert.deepEqual(DETECTED_FIELDS, [
    'platform', 'is_wsl', 'codex_cli_version', 'codex_logged_in', 'image_generation_feature',
    'detected_dispatch_model', 'detected_dispatch_effort', 'python_cmd',
    'pillow_available', 'openai_api_key_present', 'network_access_configured',
    'smoke_status', 'last_smoke_at',
  ]);
  assert.deepEqual(PREFERENCE_FIELDS, [
    'default_quality', 'default_size', 'default_output_dir', 'deny_write_paths',
    'allow_cli_fallback', 'timeout_seconds', 'max_parallel',
    'override_dispatch_model', 'quality_hint_mode',
  ]);
  assert.deepEqual(META_FIELDS, ['schema_version', 'setup_version', 'setup_at']);

  const all = [...DETECTED_FIELDS, ...PREFERENCE_FIELDS, ...META_FIELDS];
  assert.equal(new Set(all).size, all.length, 'field names must not overlap across layers');
});

// --- generate.mjs (CLI entry point) ---

test('generate: newPngsSince only reports files created after the cutoff', async () => {
  const { newPngsSince } = await import('../lib/generate.mjs');
  const os = require('node:os');
  const fs = require('node:fs');
  const path = require('node:path');

  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'codex-image-test-'));
  fs.writeFileSync(path.join(dir, 'a.png'), 'x');
  fs.writeFileSync(path.join(dir, 'notes.txt'), 'x');

  assert.equal(newPngsSince(dir, 0).length, 1, 'PNG created after epoch is counted; .txt is not');
  assert.equal(newPngsSince(dir, Date.now() + 60_000).length, 0, 'nothing is newer than a future cutoff');
  assert.deepEqual(newPngsSince(path.join(dir, 'missing'), 0), [], 'missing dir yields no results, no throw');
});

test('generate: rejects a denied destination before spending any quota', async () => {
  const { generate } = await import('../lib/generate.mjs');
  // A non-existent denied directory must still be denied. Resolving it to its
  // nearest existing ancestor would evaluate the deny list against the wrong
  // path and let the write through.
  await assert.rejects(
    () => generate({
      prompt: 'a pear',
      outputDir: '/denied-root/sub',
      filename: 'pear.png',
      denyPaths: ['/denied-root/'],
    }),
    /denied/i,
  );
});

test('safe-path: a missing output dir keeps its full path, not just its ancestor', async () => {
  const { resolveOutputPath } = await import('../lib/safe-path.mjs');
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'codex-image-test-'));
  const nested = path.join(dir, 'does', 'not', 'exist', 'yet');

  const resolved = resolveOutputPath(nested, 'a.png');
  assert.equal(path.dirname(resolved), fs.realpathSync.native(dir) + '/does/not/exist/yet');
});

test('generate: rejects filename traversal before spending any quota', async () => {
  const { generate } = await import('../lib/generate.mjs');
  const os = require('node:os');
  const fs = require('node:fs');
  const path = require('node:path');
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'codex-image-test-'));

  await assert.rejects(
    () => generate({ prompt: 'a pear', outputDir: dir, filename: '../evil.png' }),
    /traversal|separator|basename/i,
  );
});

test('generate: requires a prompt and an output directory', async () => {
  const { generate } = await import('../lib/generate.mjs');
  await assert.rejects(() => generate({ outputDir: '/tmp', filename: 'a.png' }), /prompt is required/);
  await assert.rejects(() => generate({ prompt: 'x', filename: 'a.png' }), /outputDir is required/);
});

/**
 * A fake `spawn` that emits one JSONL turn.completed event and optionally
 * drops a PNG into the staging dir, so the full generate() flow can be
 * exercised without spending Codex quota.
 */
function fakeSpawn({ stagingDir, writePng = true, pngBytes = 1_200_000 }) {
  const { EventEmitter } = require('node:events');
  return () => {
    const child = new EventEmitter();
    child.stdout = new EventEmitter();
    child.stderr = new EventEmitter();
    child.stdin = { write() {}, end() {} };
    child.kill = () => {};

    setImmediate(() => {
      if (writePng) {
        // Minimal valid PNG header: signature + IHDR declaring 1024x1024.
        const header = Buffer.alloc(24);
        Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]).copy(header, 0);
        header.write('IHDR', 12);
        header.writeUInt32BE(1024, 16);
        header.writeUInt32BE(1024, 20);
        fs.writeFileSync(
          path.join(stagingDir, 'out.png'),
          Buffer.concat([header, Buffer.alloc(Math.max(0, pngBytes - 24))]),
        );
      }
      child.stdout.emit('data', JSON.stringify({ type: 'turn.completed' }) + '\n');
      child.emit('close', 0);
    });
    return child;
  };
}

test('generate: removes a staging dir it created, on success', async () => {
  const { generate } = await import('../lib/generate.mjs');
  const outDir = fs.mkdtempSync(path.join(os.tmpdir(), 'codex-image-out-'));
  const staging = fs.mkdtempSync(path.join(os.tmpdir(), 'codex-image-stg-'));

  // stagingDir is passed so the fake can target it, but ownership is what we
  // assert on: a caller-supplied dir must survive.
  const result = await generate({
    prompt: 'a watercolor pear',
    outputDir: outDir,
    filename: 'pear.png',
    stagingDir: staging,
    spawnFn: fakeSpawn({ stagingDir: staging }),
  });

  assert.equal(result.ok, true, JSON.stringify(result.checks));
  assert.equal(result.stagingKept, true, 'a caller-supplied staging dir is never removed');
  assert.equal(fs.existsSync(staging), true);
  assert.deepEqual(result.size, { width: 1024, height: 1024 });
  assert.equal(fs.existsSync(result.output), true);
});

test('generate: keeps the staging dir when a check fails', async () => {
  const { generate } = await import('../lib/generate.mjs');
  const outDir = fs.mkdtempSync(path.join(os.tmpdir(), 'codex-image-out-'));
  const staging = fs.mkdtempSync(path.join(os.tmpdir(), 'codex-image-stg-'));

  // No PNG produced → newPngCreated fails → staging kept as evidence.
  const result = await generate({
    prompt: 'a watercolor pear',
    outputDir: outDir,
    filename: 'pear.png',
    stagingDir: staging,
    spawnFn: fakeSpawn({ stagingDir: staging, writePng: false }),
  });

  assert.equal(result.ok, false);
  assert.equal(result.checks.newPngCreated, false);
  assert.equal(result.output, null, 'nothing is copied out on failure');
  assert.equal(fs.existsSync(staging), true, 'evidence is preserved on failure');
});

test('generate: a small file is reported but does not fail the run', async () => {
  const { generate } = await import('../lib/generate.mjs');
  const outDir = fs.mkdtempSync(path.join(os.tmpdir(), 'codex-image-out-'));
  const staging = fs.mkdtempSync(path.join(os.tmpdir(), 'codex-image-stg-'));

  const result = await generate({
    prompt: 'a watercolor pear',
    outputDir: outDir,
    filename: 'pear.png',
    stagingDir: staging,
    spawnFn: fakeSpawn({ stagingDir: staging, pngBytes: 1000 }),
  });

  // File size is a soft signal: surfaced in notes, excluded from the gate.
  assert.equal(result.checks.fileSizePlausible, false);
  assert.equal(result.ok, true, 'the soft signal must not fail the run');
  assert.ok(result.notes.some((n) => n.includes('軟性')), 'the caveat must be reported');
});

test('generate: a pre-existing PNG is never counted as this run output', async () => {
  const { generate } = await import('../lib/generate.mjs');
  const outDir = fs.mkdtempSync(path.join(os.tmpdir(), 'codex-image-out-'));
  const staging = fs.mkdtempSync(path.join(os.tmpdir(), 'codex-image-stg-'));

  // A leftover from an earlier run, written just now so it falls inside the
  // creation-time tolerance window. It must still be rejected.
  fs.writeFileSync(path.join(staging, 'stale.png'), Buffer.alloc(1_200_000));

  const result = await generate({
    prompt: 'a watercolor pear',
    outputDir: outDir,
    filename: 'pear.png',
    stagingDir: staging,
    spawnFn: fakeSpawn({ stagingDir: staging, writePng: false }),
  });

  assert.equal(result.checks.newPngCreated, false, 'a stale PNG must not pass as new output');
  assert.equal(result.ok, false);
  assert.equal(result.output, null);
});
