import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { bootstrap } from '../lib/db/index.ts';
import { listPapers, upsertPaper, refreshPaper, archiveJournal } from '../lib/papers.ts';
import { publication } from '../lib/time.ts';
import { leaseTask } from '../lib/sync.ts';
import { aiSettings, filterKey, translationKey } from '../lib/ai.ts';
import { statusSnapshot } from '../lib/status.ts';
import { processAI } from '../worker/tasks.ts';

 test('跨页保持整刊分组；翻译领取时重新检查过滤，避免无效 API 调用', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'paper-opt-')), s = bootstrap(join(dir, 'db.sqlite'));
  try {
    for (const id of [1, 2]) {
      s.run('INSERT INTO journal(id,name,metadata) VALUES(?,?,?)', [id, `Journal ${id}`, '{}']);
      s.run('INSERT INTO journal_follow(journal_id,followed_at) VALUES(?,0)', [id]);
      s.run('INSERT INTO journal_stats(journal_id) VALUES(?)', [id]);
    }
    const ids: number[] = [];
    for (let i = 0; i < 45; i++) ids.push(upsertPaper(s, i % 2 ? '2' : '1', {
      doi: `10.1234/test-${i}`, title: `Title ${i}`, source: 'crossref', url: 'https://doi.org/test',
      date: publication({ published: { 'date-parts': [[2026, 10, (i % 28) + 1]] } }, '2026-10-01', '2026-10-29')
    }, Date.now()).id);
    const first = listPapers(s, { limit: 20, page: 1 });
    const second = listPapers(s, { limit: 20, page: 2 });
    const third = listPapers(s, { limit: 20, page: 3 });
    const all = [...first.items, ...second.items, ...third.items];
    assert.equal(new Set(all.map(p => p.id)).size, 45);
    assert.deepEqual(all.map(p => p.journalId), [...Array(23).fill('1'), ...Array(22).fill('2')]);
    assert.deepEqual(listPapers(s, { limit: 20, cursor: first.nextCursor! }).items, second.items);
    assert.equal(first.items.length, 20); assert.equal(second.items.length, 20);
    const oldCount = statusSnapshot(s, []).titleCount;
    archiveJournal(s, '2', 'cache-invalidation-test');
    const afterArchive = listPapers(s, { limit: 20, page: 2 });
    assert.equal(afterArchive.total, 23);
    assert.equal(afterArchive.items.length, 3);
    assert.equal(statusSnapshot(s, []).titleCount, oldCount - 22);
    s.run("UPDATE ai_settings SET base_url='https://example.com/v1',model='test',encrypted_key='not-a-real-key'");
    for (const p of s.all<{id:number;title_hash:string}>('SELECT id,title_hash FROM paper')) s.run("UPDATE task SET state='queued',payload=? WHERE kind='translate' AND paper_id=?", [JSON.stringify({cacheKey:translationKey(p.title_hash,aiSettings(s))}),p.id]);
    s.run('UPDATE journal_follow SET filter_enabled=1 WHERE journal_id=1');
    const task = leaseTask(s, 'translate')!;
    await processAI(s, [task]); // No current filter result: must defer before decrypt/network.
    assert.equal(s.one<{state:string}>('SELECT state FROM task WHERE id=?', [task.id])!.state, 'queued');
    const p = s.one<{title_hash:string}>('SELECT title_hash FROM paper WHERE id=?', [task.paper_id!])!;
    s.run("INSERT INTO paper_ai_result(paper_id,kind,cache_key,decision,created_at) VALUES(?,'filter',?,'exclude',0)", [task.paper_id!, filterKey(p.title_hash, aiSettings(s), 1)]);
    // Use the journal's actual version for this fixture.
    const version = s.one<{filter_version:number}>('SELECT filter_version FROM journal_follow WHERE journal_id=1')!.filter_version;
    s.run('UPDATE paper_ai_result SET cache_key=?', [filterKey(p.title_hash, aiSettings(s), version)]);
    s.run('UPDATE task SET next_attempt_at=0 WHERE id=?', [task.id]);
    const next = leaseTask(s, 'translate')!;
    await processAI(s, [next]);
    assert.equal(s.one<{state:string}>('SELECT state FROM task WHERE id=?', [next.id])!.state, 'cancelled');
    refreshPaper(s, task.paper_id!);
    assert.equal(s.one<{inbox_visible:number}>('SELECT inbox_visible FROM paper_listing WHERE paper_id=?', [task.paper_id!])!.inbox_visible, 0);
  } finally { s.close(); rmSync(dir, { recursive: true, force: true }); }
});
