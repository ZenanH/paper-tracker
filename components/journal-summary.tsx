'use client';
import { useCallback, useEffect, useState } from 'react';
import { api } from './client-api';
import { Icon } from './icon';

type Summary = { total: number; zones: number[]; zoneUnknown: number; top: number; ifHigh: number; ifMid: number; ifLow: number; ifUnknown: number };
export function JournalSummary({ version, search, changed, notify }: { version: number; search: () => void; changed: () => Promise<void>; notify: (notice: { message: string; tone?: 'error' }) => void }) {
 const [summary,setSummary] = useState<Summary | null>(null), [busy,setBusy] = useState(false);
 const load = useCallback(() => api<Summary>('journals/summary').then(setSummary).catch(e => notify({message:e.message,tone:'error'})),[notify]);
 useEffect(()=>{void load();},[load,version]);
 async function update() {
  setBusy(true);
  try { const result = await api<{checked:number;unmatched:number;summary:Summary}>('journals/update',{body:{}}); setSummary(result.summary); await changed(); notify({message:`Checked ${result.checked} journals.${result.unmatched ? ` ${result.unmatched} unmatched.` : ''}`}); }
  catch(e) { notify({message:(e as Error).message,tone:'error'}); }
  finally {setBusy(false);}
 }
 return <section className="journal-summary glass-lite" aria-label="Followed journal statistics">
  <div className="journal-summary-total" aria-live="polite"><span>Journals</span><strong>{summary?.total ?? '—'}</strong><small>Top <b>{summary?.top ?? '—'}</b></small></div>
  <div className="journal-stat-group" aria-label="中科院分区统计"><h2>中科院分区 <span>升级版 2025</span></h2><dl className="journal-stat-values">
   {[1,2,3,4].map(zone=><div key={zone}><dt>{zone} 区</dt><dd>{summary?.zones[zone-1] ?? '—'}</dd></div>)}
   {!!summary?.zoneUnknown && <div><dt>未知</dt><dd>{summary.zoneUnknown}</dd></div>}
  </dl></div>
  <div className="journal-stat-group journal-stat-if" aria-label="IF distribution"><h2>Impact factor <span>2025</span></h2><dl className="journal-stat-values">
   <div><dt>≥10</dt><dd>{summary?.ifHigh ?? '—'}</dd></div><div><dt>5–&lt;10</dt><dd>{summary?.ifMid ?? '—'}</dd></div><div><dt>&lt;5</dt><dd>{summary?.ifLow ?? '—'}</dd></div>
   {!!summary?.ifUnknown && <div><dt>No IF</dt><dd>{summary.ifUnknown}</dd></div>}
  </dl></div>
  <div className="journal-summary-actions"><button className="button secondary update-button" aria-busy={busy} disabled={busy} onClick={update} title="Refresh CAS 2025 and IF 2025 from the built-in catalog"><Icon name="sync" size={15}/>Update</button><button className="button primary" onClick={search}><Icon name="search" size={15}/>Search</button></div>
 </section>;
}
