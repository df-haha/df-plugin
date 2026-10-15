import fs from 'node:fs/promises';
import path from 'node:path';

const mode = process.argv[2];
const requestId = process.env.LINE_MCP_READER_REQUEST_ID;
const localAppData = process.env.LOCALAPPDATA;

if (!/^[0-9a-f]{32}$/u.test(requestId ?? '') || typeof localAppData !== 'string') {
  process.exitCode = 97;
} else {
  const directory = path.join(localAppData, 'line-desktop-mcp', 'line-reader', `line-reader-${requestId}`);
  const snapshot = path.join(directory, 'snapshot.edb');
  await fs.writeFile(snapshot, 'synthetic encrypted bytes', { flag: 'wx' });
  process.stdout.write('READY\n');

  if (mode === 'success') {
    process.stdout.write('{"ok":true}\n');
  } else if (mode === 'nonzero') {
    process.stderr.write('private synthetic stderr must not escape\n');
    process.stdout.write('{"ok":false,"code":"SOURCE_BUSY"}\n');
    process.exitCode = 2;
  } else if (mode === 'overflow') {
    process.stdout.write(Buffer.alloc(4 * 1024 * 1024 + 1, 0x78));
    setInterval(() => {}, 1_000);
  } else if (mode === 'unexpected') {
    await fs.writeFile(path.join(directory, 'unexpected-private-file'), 'do not remove');
  } else if (mode === 'reparse') {
    await fs.unlink(snapshot);
    await fs.symlink(path.dirname(directory), snapshot, 'junction');
  } else if (mode === 'hang') {
    setInterval(() => {}, 1_000);
  } else {
    process.exitCode = 98;
  }
}
