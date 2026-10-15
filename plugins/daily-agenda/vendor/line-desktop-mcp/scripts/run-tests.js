import { readdirSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

// Enumerate explicitly so npm test works in shells without glob expansion.
// Only test modules are executed; child-process fixtures are not test entries.
const directory = new URL('../test/', import.meta.url);
const files = readdirSync(directory).filter(name => name.endsWith('.test.mjs')).sort();
if (files.length === 0) throw new Error('No test modules found. Run tests from a source checkout.');
const result = spawnSync(process.execPath, ['--test', ...files.map(name => fileURLToPath(new URL(name, directory)))], {
  stdio: 'inherit',
  windowsHide: true,
});
if (result.error) throw result.error;
process.exitCode = result.status ?? 1;
