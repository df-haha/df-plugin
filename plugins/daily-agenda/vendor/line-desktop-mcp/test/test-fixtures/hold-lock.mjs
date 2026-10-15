import { withLineOperation } from '../../src/automation/line-operation-lock.mjs';

const lockPath = process.argv[2];

try {
  await withLineOperation('history-read', () => new Promise((resolve) => {
    process.stdout.write('LOCKED\n');
    process.stdin.setEncoding('utf8');
    process.stdin.once('data', resolve);
  }), { lockPath });
  process.stdout.write('RELEASED\n');
} catch (error) {
  process.stderr.write(`${error.stack ?? error}\n`);
  process.exitCode = 1;
}
