// Isolated synthetic load: no network, no credentials, no production database.
import { mkdtempSync, rmSync, writeFileSync, mkdirSync, statSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { gzipSync } from 'node:zlib';
import { performance, monitorEventLoopDelay } from 'node:perf_hooks';
import assert from 'node:assert/strict';
import { bootstrap } from '../lib/db/index.ts';
import { listPapers, archiveJournal } from '../lib/papers.ts';
import { statusSnapshot } from '../lib/status.ts';
import { leaseTask, commitTask } from '../lib/sync.ts';
import { runPool } from '../worker/pool.ts';
import { setTimeout as delay } from 'node:timers/promises';

const n = Number(process.argv[2] || 100000);
if (!Number.isSafeInteger(n) || n < 1000 || n > 1000000) throw new Error('Choose 1000–1000000 papers.');
const dir = mkdtempSync(join(tmpdir(), 'paper-benchmark-'));
const s = bootstrap(join(dir, 'benchmark.sqlite'));
const timings: Record<string, { p50: number; p95: number; max: number }> = {};
function measure(name: string, fn: () => unknown, rounds = 40) {
  const values: number[] = [];
  for (let i = 0; i < rounds; i++) { const start = performance.now(); fn(); values.push(performance.now() - start); }
  values.sort((a,b) => a-b);
  timings[name] = { p50: values[Math.floor(values.length*.5)], p95: values[Math.min(values.length-1,Math.floor(values.length*.95))], max: values.at(-1)! };
}
try {
  const start = performance.now();
  s.transaction(() => {
    s.db.exec(`WITH RECURSIVE x(i) AS (VALUES(1) UNION ALL SELECT i+1 FROM x WHERE i<1000)
      INSERT INTO journal(id,name,metadata) SELECT i,'Journal '||i,'{}' FROM x;
      INSERT INTO journal_follow(journal_id,followed_at) SELECT id,0 FROM journal;
      WITH RECURSIVE x(i) AS (VALUES(1) UNION ALL SELECT i+1 FROM x WHERE i<${n})
      INSERT INTO paper(id,doi,journal_id,title_original,title_hash,metadata_hash,published,date_precision,date_field,published_sort,date_eligible,url,source,fetched_at,created_at)
      SELECT i,'10.1234/'||i,(i-1)%1000+1,'Synthetic scientific title '||i||' about computational modeling and observations','h'||i,'m'||i,'2026-10-28','day','published','2026-10-28',1,'https://doi.org/10.1234/'||i,'crossref',0,0 FROM x;
      INSERT INTO paper_state(paper_id) SELECT id FROM paper;
      INSERT INTO paper_listing SELECT id,journal_id,published_sort,1,0,'计算建模与观测的模拟科学标题 '||id,NULL,NULL,NULL,NULL,'ready' FROM paper;
      INSERT INTO journal_stats(journal_id,visible_count) SELECT journal_id,COUNT(*) FROM paper GROUP BY journal_id;
      INSERT INTO task(paper_id,journal_id,kind,dedupe_key,payload,state,created_at,updated_at)
      SELECT id,journal_id,'translate','t'||id,'{}','succeeded',0,0 FROM paper;`);
  });
  const seedMs = performance.now()-start;
  measure('firstPageCold', () => listPapers(s,{limit:20,page:1}), 1);
  measure('statusCold', () => statusSnapshot(s, ['1','2','3']), 1);
  measure('status', () => statusSnapshot(s, ['1','2','3']));
  measure('firstPage', () => listPapers(s,{limit:20,page:1}));
  measure('middlePage', () => listPapers(s,{limit:20,page:Math.floor(n/40)}), 10);
  measure('lastPage', () => listPapers(s,{limit:20,page:Math.ceil(n/20)}), 10);
  const page = listPapers(s,{limit:20,page:1});
  measure('cursorPage', () => listPapers(s,{limit:20,cursor:page.nextCursor!}));
  const body = Buffer.from(JSON.stringify(page));
  assert.equal(page.items.length,20); assert.equal(page.total,n);
  assert.ok(body.length<=65536);
  s.run("UPDATE task SET state='queued' WHERE id<=300");
  measure('leaseAndCommit', () => { const t=leaseTask(s,'translate')!; commitTask(s,t,()=>s.run("UPDATE task SET state='succeeded',lease_owner=NULL,lease_until=NULL WHERE id=?",[t.id])); });
  // Finish active fixtures before testing user archive lock.
  s.run("UPDATE task SET state='succeeded'");
  const before = statusSnapshot(s,[]).titleCount;
  const archiveAt=performance.now(), archived=archiveJournal(s,'1','benchmark-archive');
  const archiveMs=performance.now()-archiveAt;
  assert.equal(statusSnapshot(s,[]).titleCount,before-Number(archived.count));
  const barrierAt = performance.now();
  for (let i=0;i<120;i+=3) await Promise.all([i,i+1,i+2].map(id=>delay(id===0?1000:10)));
  const barrierMs=performance.now()-barrierAt;
  s.run("UPDATE task SET state='queued' WHERE id<=120");
  const controller=new AbortController(), order:number[]=[];
  let next=0, inFlight=0, maxInFlight=0;
  const lag=monitorEventLoopDelay({resolution:10});lag.enable();
  const poolAt=performance.now();
  await runPool({slots:3,capacity:()=>3,signal:controller.signal,idleMs:1,work:async(_slot,signal)=>{
    const id=next++;if(id>=120)return false;
    const task=leaseTask(s,'translate');assert.ok(task);
    inFlight++;maxInFlight=Math.max(maxInFlight,inFlight);
    try { await delay(id===0?1000:10,undefined,{signal});order.push(id); commitTask(s,task,()=>s.run("UPDATE task SET state='succeeded',lease_owner=NULL,lease_until=NULL WHERE id=?",[task.id])); }
    finally { inFlight--; }
    if(order.length===120)controller.abort();return true;
  }});
  lag.disable();
  assert.equal(order.length,120);assert.equal(new Set(order).size,120);assert.equal(maxInFlight,3);
  const result={date:new Date().toISOString(),node:process.version,sqlite:s.one('SELECT sqlite_version() version'),papers:n,journals:1000,seedMs,timings,
    pageBytes:body.length,pageGzipBytes:gzipSync(body).length,archiveMs,archived:archived.count,
    pool:{barrierMs,requests:120,slowRequestMs:1000,fastRequestMs:10,totalMs:performance.now()-poolAt,finishedBeforeSlow:order.indexOf(0),maxInFlight,eventLoopP95Ms:lag.percentile(95)/1e6},
    rssMiB:process.memoryUsage().rss/1048576,databaseBytes:statSync(join(dir,'benchmark.sqlite')).size};
  mkdirSync('docs/performance',{recursive:true});
  writeFileSync(`docs/performance/benchmark-${n}.json`,JSON.stringify(result,null,2)+'\n');
  console.log(JSON.stringify(result,null,2));
} finally {s.close();rmSync(dir,{recursive:true,force:true});}
