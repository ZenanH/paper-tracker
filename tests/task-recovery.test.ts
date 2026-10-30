import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { bootstrap } from '../lib/db/index.ts';
import { upsertPaper, refreshPaper } from '../lib/papers.ts';
import { publication } from '../lib/time.ts';
import { statusSnapshot } from '../lib/status.ts';

test('重建投影可恢复规则切换取消的任务，但不能恢复用户 Stop 的任务', () => {
 const dir=mkdtempSync(join(tmpdir(),'paper-recovery-')),s=bootstrap(join(dir,'db.sqlite'));
 try {
  s.run("INSERT INTO journal(id,name,metadata) VALUES(1,'Test','{}')");
  s.run('INSERT INTO journal_follow(journal_id,followed_at) VALUES(1,0)');
  s.run('INSERT INTO journal_stats(journal_id) VALUES(1)');
  const p=upsertPaper(s,'1',{doi:'10.1234/recovery',title:'Test',url:'https://doi.org/test',source:'crossref',date:publication({published:{'date-parts':[[2026,10,1]]}},'2026-10-01','2026-10-29')},Date.now());
  s.run("UPDATE task SET state='cancelled',error_code=NULL,attempts=3 WHERE paper_id=?",[p.id]);
  refreshPaper(s,p.id);
  assert.equal(s.one<{state:string}>('SELECT state FROM task WHERE paper_id=?',[p.id])!.state,'blocked');
  assert.equal(s.one<{attempts:number}>('SELECT attempts FROM task WHERE paper_id=?',[p.id])!.attempts,0);
  s.run("UPDATE task SET state='cancelled',error_code='user_stopped' WHERE paper_id=?",[p.id]);
  refreshPaper(s,p.id);
  assert.equal(s.one<{state:string}>('SELECT state FROM task WHERE paper_id=?',[p.id])!.state,'cancelled');
  for(const state of ['blocked','failed','cancelled','retry','queued','running']) {
   s.run('UPDATE task SET state=?',[state]);s.touch();
   assert.equal(statusSnapshot(s,[]).active,['retry','queued','running'].includes(state));
  }
 } finally {s.close();rmSync(dir,{recursive:true,force:true});}
});

test('删除 AI key 释放更新锁，重新配置续做且旧请求被 fencing 拒绝', async () => {
 const { saveSettings, aiSettings } = await import('../lib/ai.ts');
 const { leaseTask, commitTask } = await import('../lib/sync.ts');
 const { updateRunning } = await import('../lib/update-state.ts');
 const { writeFileSync } = await import('node:fs');
 const dir=mkdtempSync(join(tmpdir(),'paper-credentials-')),s=bootstrap(join(dir,'db.sqlite'));
 const previous=process.env.ENCRYPTION_KEY_FILE;
 process.env.ENCRYPTION_KEY_FILE=join(dir,'key');writeFileSync(process.env.ENCRYPTION_KEY_FILE,'test-encryption-key-at-least-16-characters');
 try {
  const settings={baseURL:'https://example.com/v1',model:'test',filterPrompt:aiSettings(s).filter_prompt,confirmReprocess:true};
  saveSettings(s,{...settings,apiKey:'test-key'});
  s.run("INSERT INTO task(kind,dedupe_key,payload,created_at,updated_at) VALUES('translate','credential-test','{}',0,0)");
  const leased=leaseTask(s,'translate')!;
  const version=aiSettings(s).config_version;
  saveSettings(s,settings);
  assert.equal(aiSettings(s).config_version,version);
  assert.equal(s.one<{state:string}>('SELECT state FROM task WHERE id=?',[leased.id])!.state,'running');
  assert.ok(updateRunning(s));
  saveSettings(s,{...settings,deleteKey:true});
  // Initial provider change created a projection job; exclude it from this AI lock assertion.
  s.run("UPDATE task SET state='succeeded' WHERE kind='projection'");
  assert.equal(updateRunning(s),false);
  assert.equal(s.one<{state:string}>('SELECT state FROM task WHERE id=?',[leased.id])!.state,'blocked');
  assert.equal(commitTask(s,leased,()=>assert.fail('stale credential request committed')),false);
  saveSettings(s,{...settings,apiKey:'replacement-key'});
  assert.equal(s.one<{state:string}>('SELECT state FROM task WHERE id=?',[leased.id])!.state,'queued');
  assert.ok(updateRunning(s));
 } finally {s.close();rmSync(dir,{recursive:true,force:true});if(previous===undefined)delete process.env.ENCRYPTION_KEY_FILE;else process.env.ENCRYPTION_KEY_FILE=previous;}
});
