'use client';
import type { ReactNode } from 'react';
import type { Stage, StageStatus } from '../lib/status';
export type Status = { filteredCount: number; titleCount: number; lastUpdateAt: number | null; contentVersion: number; progressVersion: number; workerAlive: boolean; updating: boolean; aiConfigured: boolean; run: { id: string; kind: string; from: number; until: number; state: string } | null; stages: Stage[]; active: boolean; source: { returned: number; added: number; duplicate: number; updated: number } | null; extraTasks: number; nextScheduledAt: number; journals: { id: string; version: number; count: number; favorites: number }[] };
const labels: Record<StageStatus, string> = { queued: 'Queued', running: 'Running', retry: 'Retrying', blocked: 'Set up AI', succeeded: 'Done', partial: 'Failed', failed: 'Failed', cancelled: 'Canceled', not_needed: 'Skipped' };
const stageNames: Record<string, string> = { journal: 'Journals', fetch: 'Papers', filter: 'Filter', translate: 'Translate' };
export function Progress({ status, stale, lastChecked, actions, hideJournals = false }: { status: Status | null; stale: boolean; lastChecked: number | null; actions?: ReactNode; hideJournals?: boolean }) {
  return <section className="activity glass-lite compact-activity" aria-label="Processing progress" title={stale ? 'Connection lost' : lastChecked ? `Last checked ${new Date(lastChecked).toLocaleTimeString('en-US')}` : 'Connecting'}>
    <div className={`compact-stages ${hideJournals ? 'compact-stages-three' : ''}`}>
      {!status?.run ? <span className="muted">{status ? 'Ready' : 'Connecting…'}</span> : status.stages.filter(s => !hideJournals || s.key !== 'journal').map(s => {
        const known = s.total !== null, done = ['succeeded', 'not_needed'].includes(s.status);
        const ratio = known && s.total! > 0 ? Math.min(1, s.completed / s.total!) : done ? 1 : 0;
        const count = s.status === 'not_needed' ? 'Skipped' : known ? `${s.completed}/${s.total}` : s.completed > 0 ? `${s.completed} done` : s.status === 'running' ? 'Working' : 'Waiting';
        const description = `${stageNames[s.key] || s.label}: ${labels[s.status]}${s.failed ? `; ${s.failed} failed` : ''}${s.waiting ? `; ${s.waiting} awaiting AI configuration` : ''}${!known ? '; total pending upstream processing' : ''}`;
        return <div className="compact-stage" aria-busy={s.status === 'running' || s.status === 'retry'} key={s.key} title={description} aria-label={description}>
          <div className="compact-stage-text"><span className="compact-stage-label">{stageNames[s.key] || s.label}{s.key === 'fetch' && <span className="paper-total"> · {status.titleCount.toLocaleString('en-US')} titles</span>}{s.key === 'filter' && (status.aiConfigured || status.filteredCount > 0) && <span className="paper-total"> · {status.filteredCount.toLocaleString('en-US')} excluded</span>}</span><span className={['failed', 'partial'].includes(s.status) ? 'error-text' : done ? 'mint' : 'muted'}>{s.status === 'blocked' ? 'Set up AI' : ['failed', 'partial'].includes(s.status) ? `${count} !` : count}</span></div>
          <div className="progress-track" role="progressbar" aria-label={description} aria-valuemin={0} aria-valuemax={known ? s.total || 1 : undefined} aria-valuenow={known ? s.completed : undefined} aria-valuetext={`${count}, ${labels[s.status]}`}><span className={`progress-fill ${s.status === 'blocked' ? 'progress-paused' : ''}`} style={{ transform: `scaleX(${ratio})` }}/></div>
        </div>;
      })}
    </div>
    <div className="compact-activity-actions">{actions}</div>
    {stale && <span className="activity-offline error-text" role="status">Offline</span>}
  </section>;
}
