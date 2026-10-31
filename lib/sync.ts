import { randomUUID } from 'node:crypto';
import type { Store } from './db/index.ts';
import { beijingDate, dateShards, syncWindow } from './time.ts';
import { validISSN } from './journals/catalog.ts';
import type { FetchStep } from './sources/crossref.ts';

export type Task = { id: number; run_id: number | null; journal_id: number | null; paper_id: number | null; kind: string; dedupe_key: string; payload: string; checkpoint: string; state: string; attempts: number; lease_owner: string; lease_until: number; fence: number; completed_count: number; source_count: number; new_count: number; duplicate_count: number; updated_at: number; created_at: number };
export function enqueueSync(s: Store, targetIds?: string[], scheduleKey?: string, now = Date.now(), fresh = false) {
  return s.transaction(() => {
    const i = s.installation();
    if (scheduleKey) { const old = s.one<{ id: number }>('SELECT id FROM sync_run WHERE schedule_key=?', [scheduleKey]); if (old) return { id: String(old.id), reused: true }; }
    if (fresh) {
      s.run("UPDATE task SET state='queued',error_code=NULL,attempts=0,next_attempt_at=0 WHERE kind IN ('filter','translate','projection') AND ((state='cancelled' AND error_code='ai_connection_failed') OR state IN ('failed','retry'))");
      const running = s.one<{ run_id: number }>("SELECT run_id FROM task WHERE kind='fetch' AND state IN ('queued','running','retry') AND run_id IS NOT NULL LIMIT 1");
      if (running) return { id: String(running.run_id), reused: true };
      // Update is the single recovery entry point for source and AI work.
      s.run("UPDATE task SET state='cancelled',fence=fence+1 WHERE kind='fetch' AND state='failed'");
      s.run("UPDATE sync_run SET state='cancelled',completed_at=? WHERE state IN ('queued','running','partial','failed')", [now]);
    }
    const inProgress = s.one<{ id: number; targets: string }>("SELECT id,targets FROM sync_run WHERE state IN ('queued','running','partial','failed') ORDER BY id LIMIT 1");
    if (!targetIds && inProgress) {
      const restarted = s.run("UPDATE task SET state='queued',attempts=0,next_attempt_at=0,error_code=NULL,updated_at=? WHERE run_id=? AND kind='fetch' AND state='failed'", [now, inProgress.id]).changes;
      if (restarted) { s.run("UPDATE sync_run SET state='queued',completed_at=NULL WHERE id=?", [inProgress.id]); s.touch(); }
      return { id: String(inProgress.id), reused: true };
    }
    const targets = targetIds || s.all<{ id: string }>('SELECT CAST(journal_id AS TEXT) AS id FROM journal_follow WHERE active=1 ORDER BY journal_id').map(j => j.id);
    if (!targets.length) return { id: null, reused: false };
    const active = targets.filter(id => !s.one("SELECT id FROM task WHERE journal_id=? AND kind='fetch' AND state IN ('queued','running','retry')", [id]));
    if (!active.length) return { id: inProgress ? String(inProgress.id) : null, reused: true };
    const initial = !i.initial_sync_completed_at;
    const kind = initial ? (inProgress ? 'initial-add' : 'initial') : 'regular';
    const window = syncWindow(i.deployment_month_start, initial, now);
    const runId = Number(s.run('INSERT INTO sync_run(kind,window_start,window_end,targets,created_at,schedule_key) VALUES(?,?,?,?,?,?)', [kind, window.start, window.end, JSON.stringify(active), now, scheduleKey || null]).lastInsertRowid);
    for (const id of active) {
      const issns = s.all<{ issn: string }>('SELECT issn FROM journal_issn WHERE journal_id=?', [id]).map(r => r.issn).filter(validISSN);
      const steps: FetchStep[] = issns.flatMap(issn => dateShards(window.start, window.end).flatMap(range => [{ issn, ...range, mode: 'pub' as const }, { issn, ...range, mode: 'online' as const }]));
      s.run("INSERT INTO task(run_id,journal_id,kind,dedupe_key,payload,created_at,updated_at) VALUES(?,?,'fetch',?,?,?,?)", [runId, id, `fetch:${runId}:${id}`, JSON.stringify({ steps, from: beijingDate(window.start), until: beijingDate(window.end) }), now, now]);
    }
    s.touch();
    return { id: String(runId), reused: false };
  });
}
export function leaseTask(s: Store, kind: string, now = Date.now()): Task | undefined {
  return s.transaction(() => {
    const row = s.one<Task>("SELECT * FROM task INDEXED BY task_claim WHERE kind=? AND state IN ('queued','retry','running') AND ((state IN ('queued','retry') AND next_attempt_at<=?) OR (state='running' AND lease_until<?)) ORDER BY id LIMIT 1", [kind, now, now]);
    if (!row) return;
    const owner = randomUUID();
    s.run("UPDATE task SET state='running',lease_owner=?,lease_until=?,fence=fence+1,updated_at=? WHERE id=?", [owner, now + 120_000, now, row.id]);
    s.touch();
    return { ...row, state: 'running', lease_owner: owner, lease_until: now + 120_000, fence: row.fence + 1 };
  });
}
export function ownsTask(s: Store, t: Task, now = Date.now()) {
  return !!s.one("SELECT id FROM task WHERE id=? AND state='running' AND lease_owner=? AND fence=? AND lease_until>?", [t.id, t.lease_owner, t.fence, now]);
}
export function commitTask(s: Store, task: Task, fn: () => void) {
  return s.transaction(() => { if (!ownsTask(s, task)) return false; fn(); s.touch(); return true; });
}
export function finishRuns(s: Store, now = Date.now()) {
  s.transaction(() => {
    const runs = s.all<{ id: number; kind: string; window_end: number }>("SELECT id,kind,window_end FROM sync_run WHERE state IN ('queued','running','partial','failed') ORDER BY id");
    for (const run of runs) {
      const counts = s.all<{ state: string; n: number }>('SELECT state,COUNT(*) AS n FROM task WHERE run_id=? AND kind=\'fetch\' GROUP BY state', [run.id]);
      const n = (key: string) => counts.find(r => r.state === key)?.n || 0;
      const pending = n('queued') + n('running') + n('retry');
      const state = pending ? 'running' : n('failed') ? (n('succeeded') ? 'partial' : 'failed') : n('succeeded') ? 'succeeded' : 'cancelled';
      s.run('UPDATE sync_run SET state=?,completed_at=? WHERE id=? AND state<>?', [state, pending ? null : now, run.id, state]);
      if (state === 'succeeded' && run.kind === 'initial') {
        s.run('UPDATE installation SET initial_sync_completed_at=? WHERE id=1 AND initial_sync_completed_at IS NULL', [now]);
      }
    }
  });
}
