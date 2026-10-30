import { updateRunning } from './update-state.ts';
import type { Store } from './db/index.ts';
import { aiReady, aiSettings } from './ai.ts';

export type StageStatus = 'queued' | 'running' | 'retry' | 'blocked' | 'succeeded' | 'partial' | 'failed' | 'cancelled' | 'not_needed';
export type Stage = { key: string; label: string; status: StageStatus; completed: number; total: number | null; discovered: number; failed: number; waiting: number; nextAttemptAt: number | null };
type Count = { kind: string; state: string; n: number; next: number | null };
function stage(counts: Count[], kind: string, label: string, sealed: boolean): Stage {
  const rows = counts.filter(c => c.kind === kind);
  const number = (key: string) => rows.find(r => r.state === key)?.n || 0;
  const completed = number('succeeded'), failed = number('failed'), waiting = number('blocked');
  const discovered = rows.filter(r => r.state !== 'cancelled').reduce((n, r) => n + r.n, 0);
  const total = sealed ? discovered : null;
  const status: StageStatus = number('running') ? 'running' : number('queued') ? 'queued' : number('retry') ? 'retry' : waiting ? 'blocked' : failed ? completed ? 'partial' : 'failed' : !sealed ? 'queued' : completed ? 'succeeded' : number('cancelled') ? 'cancelled' : 'not_needed';
  return { key: kind, label, status, completed, total, discovered, failed, waiting, nextAttemptAt: rows.filter(r => r.state === 'retry').reduce<number | null>((a, b) => a === null ? b.next : Math.min(a, b.next || a), null) };
}
const translationCountsCache = new WeakMap<Store, { version: number; counts: Count[] }>();
const countsCache = new WeakMap<Store, { version: number; runId: number | null; counts: Count[] }>();
export function statusSnapshot(s: Store, scope: string[]) {
  const run = s.one<{ id: number; kind: string; window_start: number; window_end: number; targets: string; state: string }>("SELECT r.* FROM sync_run r WHERE EXISTS(SELECT 1 FROM task t WHERE t.run_id=r.id AND t.state IN ('queued','running','retry','blocked','failed')) ORDER BY EXISTS(SELECT 1 FROM task f WHERE f.run_id=r.id AND f.kind='fetch' AND f.state IN ('queued','running','retry')) DESC,r.id DESC LIMIT 1") || s.one<{ id: number; kind: string; window_start: number; window_end: number; targets: string; state: string }>('SELECT * FROM sync_run ORDER BY id DESC LIMIT 1');
  const version = s.installation().progress_version;
  const cached = countsCache.get(s);
  const counts = cached?.version === version && cached.runId === (run?.id ?? null) ? cached.counts : [
    ...s.all<Count>(`SELECT kind,state,COUNT(*) AS n,MIN(next_attempt_at) AS next FROM task WHERE kind='fetch' AND ${run ? 'run_id=?' : 'run_id IS NULL'} GROUP BY kind,state`, run ? [run.id] : []),
    ...s.all<Count>("SELECT kind,state,COUNT(*) AS n,MIN(next_attempt_at) AS next FROM task WHERE kind IN ('filter','translate') AND state<>'cancelled' GROUP BY kind,state")
  ];
  if (cached?.counts !== counts) countsCache.set(s, { version, runId: run?.id ?? null, counts });
  const fetchPending = counts.some(c => c.kind === 'fetch' && ['queued', 'running', 'retry'].includes(c.state));
  const filterPending = counts.some(c => c.kind === 'filter' && ['queued', 'running', 'retry', 'blocked'].includes(c.state));
  const stages = [stage(counts, 'fetch', '论文抓取', true), stage(counts, 'filter', 'AI 过滤', !fetchPending), stage(counts, 'translate', '标题翻译', !fetchPending && !filterPending)];
  if (!counts.some(c => c.kind === 'filter' && c.state !== 'cancelled') && !s.one('SELECT journal_id FROM journal_follow WHERE active=1 AND filter_enabled=1 LIMIT 1')) {
    stages[1] = { ...stages[1], status: 'not_needed', total: 0 };
  }
  const settings = aiSettings(s), installation = s.installation();
  const translationCached = translationCountsCache.get(s);
  const translationCounts = translationCached?.version === version ? translationCached.counts : s.all<Count>(`
    SELECT 'translate' kind,
      CASE WHEN l.title_zh IS NOT NULL AND l.title_zh<>'' THEN 'succeeded'
           WHEN t.state='succeeded' THEN 'failed'
           ELSE COALESCE(t.state,?) END state,COUNT(*) n,MIN(t.next_attempt_at) next
    FROM paper_listing l LEFT JOIN task t
      ON t.dedupe_key=CAST(l.paper_id AS TEXT)||':translate:'||l.translation_key
    WHERE l.inbox_visible=1 GROUP BY CASE WHEN l.title_zh IS NOT NULL AND l.title_zh<>'' THEN 'succeeded' WHEN t.state='succeeded' THEN 'failed' ELSE COALESCE(t.state,?) END`, [aiReady(settings) ? 'queued' : 'blocked', aiReady(settings) ? 'queued' : 'blocked']);
  if (translationCached?.counts !== translationCounts) translationCountsCache.set(s, { version, counts: translationCounts });
  const translationStage = stage(translationCounts, 'translate', '标题翻译', true);
  // The denominator is the current inbox, including untranslated/canceled items.
  translationStage.total = translationCounts.reduce((sum, row) => sum + row.n, 0);
  translationStage.discovered = translationStage.total;
  if (translationStage.completed < translationStage.total && ['succeeded', 'not_needed'].includes(translationStage.status)) translationStage.status = 'cancelled';
  stages[2] = translationStage;
  const journals = scope.length ? s.all<{ id: string; version: number; count: number; favorites: number }>(`SELECT CAST(journal_id AS TEXT) AS id,version,visible_count AS count,favorite_count AS favorites FROM journal_stats WHERE journal_id IN(${scope.map(() => '?').join(',')})`, scope.slice(0, 20)) : [];
  const source = run ? s.one<{ returned: number; added: number; duplicate: number; updated: number }>("SELECT COALESCE(SUM(source_count),0) AS returned,COALESCE(SUM(new_count),0) AS added,COALESCE(SUM(duplicate_count),0) AS duplicate,COALESCE(MAX(updated_at),0) AS updated FROM task WHERE run_id=? AND kind='fetch'", [run.id]) : null;
  const extra = s.one<{ n: number }>(`SELECT COUNT(*) AS n FROM task WHERE state IN ('queued','running','retry','blocked','failed') ${run ? 'AND (run_id IS NULL OR run_id<>?)' : ''}`, run ? [run.id] : [])!.n;
  return {
    filteredCount: s.one<{ n: number }>(`SELECT COUNT(*) AS n FROM paper_listing l JOIN paper_state ps ON ps.paper_id=l.paper_id JOIN paper p ON p.id=l.paper_id JOIN journal_follow f ON f.journal_id=l.journal_id WHERE f.active=1 AND f.filter_enabled=1 AND p.date_eligible=1 AND ps.archived_at IS NULL AND l.decision='exclude' AND COALESCE(ps.manual_filter_override,'')<>'keep'`)!.n,
    titleCount: s.one<{ n: number }>('SELECT COALESCE(SUM(visible_count),0) AS n FROM journal_stats')!.n,
    lastUpdateAt: s.one<{ at: number | null }>('SELECT MAX(created_at) AS at FROM sync_run')?.at ?? null,
    contentVersion: installation.content_version, progressVersion: installation.progress_version,
    workerAlive: !!installation.worker_heartbeat_at && Date.now() - installation.worker_heartbeat_at < 25_000,
    aiConfigured: aiReady(settings),
    run: run ? { id: String(run.id), kind: run.kind, from: run.window_start, until: run.window_end, state: run.state } : null,
    stages: [{ key: 'journal', label: 'Journal details', status: 'succeeded' as StageStatus, completed: run ? (JSON.parse(run.targets) as string[]).length : journals.length, total: run ? (JSON.parse(run.targets) as string[]).length : journals.length, discovered: 0, failed: 0, waiting: 0, nextAttemptAt: null }, ...stages],
    source, journals, extraTasks: extra,
    updating: updateRunning(s),
    active: updateRunning(s),
    nextScheduledAt: installation.next_scheduled_at
  };
}
