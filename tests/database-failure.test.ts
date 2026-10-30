import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { bootstrap, Store } from '../lib/db/index.ts';

test('SQLite FULL 自动回滚后保留原始错误，不提交用户状态或检查点', () => {
 const dir=mkdtempSync(join(tmpdir(),'paper-full-')),s=bootstrap(join(dir,'db.sqlite'));
 try {
  s.db.exec('CREATE TABLE capacity_test (id INTEGER PRIMARY KEY,value BLOB)');
  s.run('INSERT INTO capacity_test VALUES(1,?)',['before']);
  s.run("INSERT INTO task(kind,dedupe_key,payload,created_at,updated_at) VALUES('fetch','full-test','{}',0,0)");
  const pages=s.one<{page_count:number}>('PRAGMA page_count')!.page_count;
  s.db.exec(`PRAGMA max_page_count=${pages}`);
  assert.throws(()=>s.transaction(()=>{
   s.run("UPDATE capacity_test SET value='uncommitted' WHERE id=1");
   s.run("UPDATE task SET checkpoint='uncommitted',state='succeeded'");
   s.db.exec('INSERT INTO capacity_test(value) VALUES(randomblob(1048576))');
  }), (e:unknown)=>e instanceof Error && 'errcode' in e && e.errcode===13);
  assert.equal(s.one<{value:string}>('SELECT value FROM capacity_test WHERE id=1')!.value,'before');
  assert.deepEqual({...s.one('SELECT state,checkpoint FROM task')!},{state:'queued',checkpoint:'{}'});
  assert.equal(s.db.isTransaction,false);
  s.transaction(()=>s.run("UPDATE capacity_test SET value='ok' WHERE id=1"));
  assert.equal(s.one<{value:string}>('SELECT value FROM capacity_test WHERE id=1')!.value,'ok');
 } finally {s.close();rmSync(dir,{recursive:true,force:true});}
});

test('常规连接不执行 schema DDL，bootstrap 补充迁移保持幂等', () => {
 const dir=mkdtempSync(join(tmpdir(),'paper-schema-')),path=join(dir,'db.sqlite');
 let s=bootstrap(path);
 try {
  s.db.exec('DROP INDEX filtered_listing');s.close();
  s=new Store(path);
  assert.equal(s.one("SELECT name FROM sqlite_master WHERE name='filtered_listing'"),undefined);
  s.close();s=bootstrap(path);
  assert.ok(s.one("SELECT name FROM sqlite_master WHERE name='filtered_listing'"));
  const installed=s.installation().installed_at;s.close();s=bootstrap(path);
  assert.equal(s.installation().installed_at,installed);
 } finally {s.close();rmSync(dir,{recursive:true,force:true});}
});
