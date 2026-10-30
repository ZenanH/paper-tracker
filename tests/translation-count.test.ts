import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { bootstrap } from '../lib/db/index.ts';
import { archiveJournal, upsertPaper } from '../lib/papers.ts';
import { publication } from '../lib/time.ts';
import { statusSnapshot } from '../lib/status.ts';

test('翻译统计仅计当前收件箱，归档收藏和历史任务不计入', () => {
 const dir=mkdtempSync(join(tmpdir(),'paper-translation-count-')),s=bootstrap(join(dir,'db.sqlite'));
 try {
  for(const id of [1,2]) {
   s.run('INSERT INTO journal(id,name,metadata) VALUES(?,?,?)',[id,`Journal ${id}`,'{}']);
   s.run('INSERT INTO journal_follow(journal_id,followed_at) VALUES(?,0)',[id]);
   s.run('INSERT INTO journal_stats(journal_id) VALUES(?)',[id]);
  }
  for(let i=1;i<=4;i++) {
   const p=upsertPaper(s,i<=2?'1':'2',{doi:`10.1234/${i}`,title:`Title ${i}`,url:'https://doi.org/test',source:'crossref',date:publication({published:{'date-parts':[[2026,10,1]]}},'2026-10-01','2026-10-29')},Date.now());
   if(i<=3)s.run("UPDATE paper_listing SET title_zh='中文标题',ai_status='ready' WHERE paper_id=?",[p.id]);
  }
  s.touch(true);
  const check=(completed:number,total:number)=>{
   const snapshot=statusSnapshot(s,[]),stage=snapshot.stages.find(s=>s.key==='translate')!;
   assert.equal(stage.completed,completed);assert.equal(stage.total,total);assert.equal(snapshot.titleCount,total);
  };
  check(3,4);
  s.run('UPDATE paper_state SET favorited_at=1 WHERE paper_id=1');
  archiveJournal(s,'1','archive-one');check(1,2);
  assert.equal(s.one<{title_zh:string}>('SELECT title_zh FROM paper_listing WHERE paper_id=1')!.title_zh,'中文标题');
  // Exclusion removes a paper from both denominators without deleting its translation.
  s.run('UPDATE paper_listing SET inbox_visible=0 WHERE paper_id=3');
  s.run('UPDATE journal_stats SET visible_count=visible_count-1 WHERE journal_id=2');s.touch(true);check(0,1);
  archiveJournal(s,'2','archive-two');check(0,0);
 } finally {s.close();rmSync(dir,{recursive:true,force:true});}
});

test('已完成译题与取消/丢失结果混合时不误报翻译全部完成', () => {
 const dir=mkdtempSync(join(tmpdir(),'paper-translation-status-')),s=bootstrap(join(dir,'db.sqlite'));
 try {
  s.run("INSERT INTO journal(id,name,metadata) VALUES(1,'Test','{}')");
  s.run('INSERT INTO journal_follow(journal_id,followed_at) VALUES(1,0)');
  s.run('INSERT INTO journal_stats(journal_id) VALUES(1)');
  for(let i=1;i<=2;i++) upsertPaper(s,'1',{doi:`10.1234/status-${i}`,title:`Title ${i}`,url:'https://example.com',source:'test',date:publication({published:{'date-parts':[[2026,10,1]]}},'2026-10-01','2026-10-29')},Date.now());
  s.run("UPDATE paper_listing SET title_zh='中文' WHERE paper_id=1");
  s.run("UPDATE task SET state='cancelled' WHERE paper_id=2");s.touch();
  let stage=statusSnapshot(s,[]).stages.find(s=>s.key==='translate')!;
  assert.equal(stage.completed,1);assert.equal(stage.total,2);assert.equal(stage.status,'cancelled');
  s.run("UPDATE task SET state='succeeded' WHERE paper_id=2");s.touch();
  stage=statusSnapshot(s,[]).stages.find(s=>s.key==='translate')!;
  assert.equal(stage.status,'partial');assert.equal(stage.failed,1);
 } finally {s.close();rmSync(dir,{recursive:true,force:true});}
});
