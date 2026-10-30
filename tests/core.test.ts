import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { bootstrap } from '../lib/db/index.ts';
import { monthStart, subtractMonths, syncWindow, publication } from '../lib/time.ts';
import { upsertPaper, archiveJournal, favoritePaper } from '../lib/papers.ts';
import { leaseTask, commitTask } from '../lib/sync.ts';
import { splitISSNs } from '../lib/journals/catalog.ts';
import { adaptWork } from '../lib/sources/crossref.ts';

test('北京时间部署月与三个日历月下界', () => {
  assert.equal(new Date(monthStart(Date.parse('2026-09-30T17:00:00Z'))).toISOString(), '2026-09-30T16:00:00.000Z');
  assert.equal(new Date(subtractMonths(Date.parse('2027-05-31T02:00:00Z'), 3)).toISOString(), '2027-02-28T02:00:00.000Z');
  const d = monthStart(Date.parse('2026-10-28T01:00:00Z'));
  assert.equal(syncWindow(d, false, Date.parse('2026-11-15T01:00:00Z')).start, d);
});
test('出版日期保留精度并优先 online first', () => {
  const p = publication({ 'published-online': { 'date-parts': [[2026, 10]] }, 'published-print': { 'date-parts': [[2027, 1, 1]] } }, '2026-10-20', '2026-10-28');
  assert.equal(p.precision, 'month'); assert.equal(p.value, '2026-10'); assert.equal(p.eligible, true);
  assert.equal(publication({ published: { 'date-parts': [[2026]] } }, '2026-10-01', '2026-10-28').eligible, false);
});
test('Crossref 适配器丢弃摘要和非本刊记录', () => {
  const raw = { DOI: '10.1234/example', title: ['<i>A title</i>'], ISSN: ['0028-0836'], abstract: 'MUST NEVER PERSIST', published: { 'date-parts': [[2026, 10, 1]] } };
  const result = adaptWork(raw, ['0028-0836'], '2026-10-01', '2026-10-28');
  assert.equal(result?.title, 'A title'); assert.ok(!JSON.stringify(result).includes('MUST NEVER'));
  assert.equal(adaptWork(raw, ['0036-8075'], '2026-10-01', '2026-10-28'), null);
});
test('归档快照、重复抓取与收藏状态独立且安装基准不变', () => {
  const dir = mkdtempSync(join(tmpdir(), 'paper-core-')); const path = join(dir, 'test.sqlite');
  let s = bootstrap(path, Date.parse('2026-10-28T01:00:00Z'));
  const installed = s.installation().installed_at;
  try {
    s.run("INSERT INTO journal(id,name,metadata) VALUES(1,'Test','{}')");
    s.run('INSERT INTO journal_follow(journal_id,followed_at) VALUES(1,?)', [Date.now()]);
    s.run('INSERT INTO journal_stats(journal_id) VALUES(1)');
    const paper = (doi: string) => ({ doi, title: 'A title', url: `https://doi.org/${doi}`, date: publication({ published: { 'date-parts': [[2026, 10, 1]] } }, '2026-10-01', '2026-10-28'), source: 'crossref' });
    const first = s.transaction(() => upsertPaper(s, '1', paper('10.1234/one'), Date.now()));
    favoritePaper(s, String(first.id), true, 'favorite-one');
    const archived = archiveJournal(s, '1', 'archive-one'); assert.equal(archived.count, 1);
    s.transaction(() => upsertPaper(s, '1', paper('10.1234/one'), Date.now()));
    const state = s.one<{ archived_at: number; favorited_at: number }>('SELECT * FROM paper_state WHERE paper_id=?', [first.id])!;
    assert.ok(state.archived_at); assert.ok(state.favorited_at);
    const second = s.transaction(() => upsertPaper(s, '1', paper('10.1234/two'), Date.now()));
    assert.equal(s.one<{ archived_at: null }>('SELECT archived_at FROM paper_state WHERE paper_id=?', [second.id])!.archived_at, null);
    assert.equal(archiveJournal(s, '1', 'archive-one').count, 1);
    s.close(); s = bootstrap(path, Date.parse('2027-01-01T01:00:00Z'));
    assert.equal(s.installation().installed_at, installed);
  } finally { s.close(); rmSync(dir, { recursive: true, force: true }); }
});
test('过期 worker 不可提交新租约的任务', () => {
  const dir = mkdtempSync(join(tmpdir(), 'paper-lease-')); const s = bootstrap(join(dir, 'test.sqlite'));
  try {
    s.run("INSERT INTO task(kind,dedupe_key,payload,created_at,updated_at) VALUES('fetch','lease-test','{}',0,0)");
    const first = leaseTask(s, 'fetch', Date.now())!;
    s.run('UPDATE task SET lease_until=0 WHERE id=?', [first.id]);
    const second = leaseTask(s, 'fetch', Date.now())!;
    assert.ok(second.fence > first.fence);
    let changed = false; assert.equal(commitTask(s, first, () => { changed = true; }), false); assert.equal(changed, false);
    assert.equal(commitTask(s, second, () => {}), true);
  } finally { s.close(); rmSync(dir, { recursive: true, force: true }); }
});

test('缺失刊号 N/A 不能拆成共享身份', () => {
  assert.deepEqual(splitISSNs('N/A/2041-1723'), ['2041-1723']);
});
