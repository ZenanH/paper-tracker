import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { bootstrap, Store } from '../lib/db/index.ts';
import { updateRunning } from '../lib/update-state.ts';
import { archiveJournal, favoritePaper } from '../lib/papers.ts';

test('更新锁来自持久任务，跨连接生效；终止或等待配置后解除', () => {
 const dir = mkdtempSync(join(tmpdir(), 'paper-update-')), path = join(dir, 'test.sqlite');
 const s = bootstrap(path), second = new Store(path);
 try {
  s.run("INSERT INTO task(kind,dedupe_key,payload,state,created_at,updated_at) VALUES('fetch','test','{}','queued',0,0)");
  for (const state of ['queued','running','retry']) {
   s.run('UPDATE task SET state=?', [state]);
   assert.equal(updateRunning(second), true);
   assert.throws(() => archiveJournal(second, '1', 'archive-test'), /Update in progress/);
   assert.throws(() => favoritePaper(second, '1', true, 'favorite-test'), /Update in progress/);
  }
  for (const state of ['succeeded','failed','blocked','cancelled']) {
   s.run('UPDATE task SET state=?', [state]); assert.equal(updateRunning(second), false);
  }
 } finally { second.close(); s.close(); rmSync(dir,{recursive:true,force:true}); }
});
