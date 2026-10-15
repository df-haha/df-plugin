import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { configuredPythonPath } from '../src/extensions/line-runtime.mjs';

const executable = configuredPythonPath();
if (!executable) {
  console.error('Set LINE_MCP_PYTHON to an absolute Python executable before running reader tests.');
  process.exitCode = 1;
} else {
  const result = spawnSync(executable, ['-B', '-m', 'unittest', 'discover', '-s',
    fileURLToPath(new URL('../test/python/', import.meta.url)), '-p', 'test_*.py', '-v'],
  { stdio: 'inherit', windowsHide: true, shell: false });
  if (result.error) {
    console.error('The configured Python test runtime could not start.');
    process.exitCode = 1;
  } else process.exitCode = result.status ?? 1;
}
