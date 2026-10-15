import assert from 'node:assert/strict';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

import { PACKAGE_ROOT, runtimeRequire, configuredPythonPath } from '../src/extensions/line-runtime.mjs';

test('resolves runtime dependencies from this package root', () => {
  const expectedRoot = path.dirname(fileURLToPath(new URL('../package.json', import.meta.url)));
  assert.equal(PACKAGE_ROOT, expectedRoot);
  assert.equal(runtimeRequire().resolve('./package.json'), path.join(expectedRoot, 'package.json'));
});

test('optional Python requires an explicit absolute executable without shell or PATH interpretation', t => {
  const previous = process.env.LINE_MCP_PYTHON;
  t.after(() => {
    if (previous === undefined) delete process.env.LINE_MCP_PYTHON;
    else process.env.LINE_MCP_PYTHON = previous;
  });
  delete process.env.LINE_MCP_PYTHON;
  assert.equal(configuredPythonPath(), null);
  for (const value of [null, '', 'python', 'python.exe', './python.exe', ' python.exe ', 'python.exe\0', 'C:/Tools/python.cmd']) {
    assert.equal(configuredPythonPath(value), null);
  }
  const executable = path.resolve('example-runtime', 'python.exe');
  assert.equal(configuredPythonPath(executable), executable);
  process.env.LINE_MCP_PYTHON = executable;
  assert.equal(configuredPythonPath(), executable);
  assert.equal(configuredPythonPath(undefined), executable);
  process.env.LINE_MCP_PYTHON = 'python.exe';
  assert.equal(configuredPythonPath(), null);
});
