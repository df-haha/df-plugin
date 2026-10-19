import { describe, it, after } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync, mkdtempSync, rmSync, symlinkSync, writeFileSync, realpathSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { draftKeys, isTaintedDraft, checkReadTaint, checkBashTaint } from './lock-core.mjs';

// Tainted-drafts record/check must agree when the draft is written through a
// symlinked parent and later shown via the real path (and vice versa).
describe('tainted drafts: symlinked parent', () => {
  const root = realpathSync(mkdtempSync(join(tmpdir(), 'da-draftkeys-')));
  const home = join(root, 'home');
  const realDir = join(home, 'runs', 'drafts', 'abc');
  const linkRoot = join(root, 'link-home');
  mkdirSync(realDir, { recursive: true });
  symlinkSync(home, linkRoot, 'dir');
  const viaLink = join(linkRoot, 'runs', 'drafts', 'abc', 'criteria-draft.md');
  const viaReal = join(realDir, 'criteria-draft.md');

  after(() => rmSync(root, { recursive: true, force: true }));

  it('record via symlink (file not yet written) matches check via real path', () => {
    const recorded = new Set(draftKeys(viaLink));
    writeFileSync(viaReal, '# draft', 'utf8');
    assert.equal(isTaintedDraft(recorded, viaReal), true);
    assert.equal(checkReadTaint(viaReal, home, [], undefined, recorded).taint, true);
  });

  it('record via real path matches check via symlink', () => {
    const recorded = new Set(draftKeys(viaReal));
    assert.equal(isTaintedDraft(recorded, viaLink), true);
  });

  it('draft show --file via real path is tainted when recorded via symlink', () => {
    const recorded = new Set(draftKeys(viaLink));
    const cmd = `uv run --quiet --project "\${CLAUDE_PLUGIN_ROOT}/scripts" python -I -X utf8 -m daily_agenda draft show --file "${viaReal}"`;
    const r = checkBashTaint(cmd, { daHome: home, taintedDrafts: recorded });
    assert.equal(r.taint, true);
  });

  it('an unrelated draft is not flagged', () => {
    const recorded = new Set(draftKeys(viaLink));
    const other = join(home, 'runs', 'drafts', 'zzz', 'criteria-draft.md');
    assert.equal(isTaintedDraft(recorded, other), false);
  });
});
