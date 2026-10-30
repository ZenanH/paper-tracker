import { requireUpdateIdle } from './update-state.ts';
import type { Store } from './db/index.ts';
import { aiReady, aiSettings, filterKey, translationKey } from './ai.ts';
import { AppError, hash, encodeCursor, decodeCursor } from './security.ts';
import type { Publication } from './time.ts';

export type PaperInput = { doi: string; title: string; url: string; date: Publication; source: string };
export function normalizeDOI(value: string): string {
  let doi = value.trim().replace(/^(?:doi:\s*|https?:\/\/(?:dx\.)?doi\.org\/)/i, '');
  try { doi = decodeURIComponent(doi); } catch { /* Retain legal literal percent if source is not URL-encoded. */ }
  return doi.trim().toLowerCase();
}
export function upsertPaper(s: Store, journalId: string, paper: PaperInput, now: number, runId: number | null = null) {
  const doi = normalizeDOI(paper.doi);
  const existing = s.one<{ id: number; metadata_hash: string; title_hash: string; journal_id: number }>('SELECT id,metadata_hash,title_hash,journal_id FROM paper WHERE doi=?', [doi]);
  if (existing && String(existing.journal_id) !== journalId) return { id: existing.id, created: false, duplicate: true };
  const titleHash = hash(paper.title), metadataHash = hash(JSON.stringify([paper.title, paper.url, paper.date]));
  if (existing?.metadata_hash === metadataHash) return { id: existing.id, created: false, duplicate: true };
  let id: number;
  if (existing) {
    id = existing.id;
    s.run('UPDATE paper SET title_original=?,title_hash=?,metadata_hash=?,published=?,date_precision=?,date_field=?,published_sort=?,date_eligible=?,url=?,fetched_at=? WHERE id=?', [paper.title, titleHash, metadataHash, paper.date.value, paper.date.precision, paper.date.field, paper.date.sort, +paper.date.eligible, paper.url, now, id]);
  } else {
    id = Number(s.run('INSERT INTO paper(doi,journal_id,title_original,title_hash,metadata_hash,published,date_precision,date_field,published_sort,date_eligible,url,source,fetched_at,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)', [doi, journalId, paper.title, titleHash, metadataHash, paper.date.value, paper.date.precision, paper.date.field, paper.date.sort, +paper.date.eligible, paper.url, paper.source, now, now]).lastInsertRowid);
    s.run('INSERT INTO paper_state(paper_id) VALUES(?)', [id]);
    s.run('INSERT OR IGNORE INTO paper_external_id VALUES(?,?,?)', [paper.source, doi, id]);
  }
  refreshPaper(s, id, runId);
  return { id, created: !existing, duplicate: false };
}
type Projection = { paper_id: number; journal_id: number; published_sort: string; inbox_visible: number; favorited: number; title_zh: string | null; decision: string | null; reason: string | null; filter_key: string | null; translation_key: string | null; ai_status: string };
export function refreshPaper(s: Store, id: number | string, runId: number | null = null) {
  const p = s.one<{ id: number; journal_id: number; title_hash: string; published_sort: string; date_eligible: number; archived_at: number | null; favorited_at: number | null; manual_filter_override: string | null; active: number; filter_enabled: number; filter_version: number }>('SELECT p.*,ps.*,f.active,f.filter_enabled,f.filter_version FROM paper p JOIN paper_state ps ON ps.paper_id=p.id JOIN journal_follow f ON f.journal_id=p.journal_id WHERE p.id=?', [id]);
  if (!p) return;
  const settings = aiSettings(s), fk = filterKey(p.title_hash, settings, p.filter_version), tk = translationKey(p.title_hash, settings);
  const f = s.one<{ decision: string; reason: string }>("SELECT decision,reason FROM paper_ai_result WHERE paper_id=? AND kind='filter' AND cache_key=?", [id, fk]);
  const t = s.one<{ title_zh: string }>("SELECT title_zh FROM paper_ai_result WHERE paper_id=? AND kind='translate' AND cache_key=?", [id, tk]);
  const visible = !!(p.active && p.date_eligible && p.archived_at === null && (!p.filter_enabled || f?.decision !== 'exclude' || p.manual_filter_override === 'keep'));
  const favorite = p.favorited_at !== null;
  const needsProcessing = !!(p.date_eligible && p.active && p.archived_at === null || favorite);
  const needsFilter = needsProcessing && !!p.filter_enabled && !p.manual_filter_override && !f;
  const needsTranslation = needsProcessing && !needsFilter && (!p.filter_enabled || f?.decision !== 'exclude' || favorite || p.manual_filter_override === 'keep') && !t;
  const latestFailure = s.one<{ state: string; error_code: string | null }>("SELECT state,error_code FROM task WHERE paper_id=? AND kind IN ('filter','translate') AND state IN ('failed','retry','blocked') ORDER BY id DESC LIMIT 1", [id]);
  const aiStatus = !needsProcessing ? 'skipped' : !aiReady(settings) && (needsFilter || needsTranslation) ? 'needs_config' : latestFailure?.state === 'failed' && (needsFilter || needsTranslation) ? 'failed' : needsFilter ? 'filtering' : needsTranslation ? 'translating' : p.filter_enabled && f?.decision === 'exclude' ? 'excluded' : 'ready';
  const next: Projection = { paper_id: p.id, journal_id: p.journal_id, published_sort: p.published_sort, inbox_visible: +visible, favorited: +favorite, title_zh: t?.title_zh || null, decision: p.filter_enabled ? f?.decision || null : null, reason: p.filter_enabled ? f?.reason || null : null, filter_key: fk, translation_key: tk, ai_status: aiStatus };
  const old = s.one<Projection>('SELECT * FROM paper_listing WHERE paper_id=?', [id]);
  if (!old || Object.keys(next).some(key => next[key as keyof Projection] !== old[key as keyof Projection])) {
    s.run('INSERT INTO paper_listing VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(paper_id) DO UPDATE SET published_sort=excluded.published_sort,inbox_visible=excluded.inbox_visible,favorited=excluded.favorited,title_zh=excluded.title_zh,decision=excluded.decision,reason=excluded.reason,filter_key=excluded.filter_key,translation_key=excluded.translation_key,ai_status=excluded.ai_status', Object.values(next));
    s.run('UPDATE journal_stats SET visible_count=visible_count+?,favorite_count=favorite_count+?,version=version+1 WHERE journal_id=?', [next.inbox_visible - (old?.inbox_visible || 0), next.favorited - (old?.favorited || 0), p.journal_id]);
    s.touch(true);
  }
  for (const [kind, key, needed] of [['filter', fk, needsFilter], ['translate', tk, needsTranslation]] as const) {
    if (needed) s.run("INSERT OR IGNORE INTO task(run_id,journal_id,paper_id,kind,dedupe_key,payload,state,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(dedupe_key) DO UPDATE SET state=excluded.state,next_attempt_at=0,attempts=0,error_code=NULL,updated_at=excluded.updated_at WHERE task.state='cancelled' AND COALESCE(task.error_code,'')<>'user_stopped'", [runId, p.journal_id, id, kind, `${id}:${kind}:${key}`, JSON.stringify({ cacheKey: key }), aiReady(settings) ? 'queued' : 'blocked', Date.now(), Date.now()]);
  }
}
export function refreshJournal(s: Store, journalId: string) {
  // Bounded projection task; no network or giant client-side ID list.
  const now = Date.now();
  s.run("INSERT OR IGNORE INTO task(journal_id,kind,dedupe_key,payload,created_at,updated_at) VALUES(?,'projection',?,'{}',?,?)", [journalId, `journal-projection:${journalId}:${now}`, now, now]);
  s.touch();
}
function previousOperation(s: Store, token: string, kind: string, target: string) {
  const existing = s.one<{ kind: string; target: string; result: string }>('SELECT * FROM operation WHERE token=?', [token]);
  if (existing && (existing.kind !== kind || existing.target !== target)) throw new AppError('操作 token 已用于其他请求。', 409);
  return existing ? JSON.parse(existing.result) as Record<string, unknown> : null;
}
export function favoritePaper(s: Store, id: string, value: boolean, token: string) {
  return s.transaction(() => {
    const previous = previousOperation(s, token, 'favorite', `${id}:${value}`); if (previous) return previous;
    requireUpdateIdle(s);
    if (!s.one('SELECT paper_id FROM paper_state WHERE paper_id=?', [id])) throw new AppError('论文不存在。', 404);
    s.run('UPDATE paper_state SET favorited_at=?,version=version+1 WHERE paper_id=? AND (favorited_at IS NOT NULL)<>?', [value ? Date.now() : null, id, +value]);
    refreshPaper(s, id);
    const result = { id, favorite: value, operationId: token };
    s.run('INSERT INTO operation VALUES(?,?,?,?,?,NULL)', [token, 'favorite', `${id}:${value}`, JSON.stringify(result), Date.now()]);
    return result;
  });
}
export function archiveJournal(s: Store, journalId: string, token: string) {
  return s.transaction(() => {
    const previous = previousOperation(s, token, 'archive', journalId); if (previous) return previous;
    requireUpdateIdle(s);
    if (!s.one('SELECT id FROM journal WHERE id=?', [journalId])) throw new AppError('期刊不存在。', 404);
    const now = Date.now(), expires = now + 30_000;
    const ceiling = s.one<{ id: number }>('SELECT COALESCE(MAX(id),0) AS id FROM paper')!.id;
    s.run('INSERT INTO operation VALUES(?,?,?,?,?,?)', [token, 'archive', journalId, '{}', now, expires]);
    s.run('INSERT INTO archive_item SELECT ?,p.id FROM paper p JOIN paper_state ps ON ps.paper_id=p.id WHERE p.journal_id=? AND p.id<=? AND ps.archived_at IS NULL', [token, journalId, ceiling]);
    const count = s.one<{ n: number }>('SELECT COUNT(*) AS n FROM archive_item WHERE operation_token=?', [token])!.n;
    s.run('UPDATE paper_state SET archived_at=?,archive_operation=?,version=version+1 WHERE paper_id IN (SELECT paper_id FROM archive_item WHERE operation_token=?)', [now, token, token]);
    const removed = s.one<{ n: number }>('SELECT COALESCE(SUM(inbox_visible),0) AS n FROM paper_listing WHERE paper_id IN (SELECT paper_id FROM archive_item WHERE operation_token=?)', [token])!.n;
    s.run('UPDATE paper_listing SET inbox_visible=0 WHERE paper_id IN (SELECT paper_id FROM archive_item WHERE operation_token=?)', [token]);
    s.run('UPDATE journal_stats SET visible_count=visible_count-?,version=version+1 WHERE journal_id=?', [removed, journalId]);
    const result = { count, operationId: token, undoExpiresAt: expires, snapshotSeq: String(ceiling) };
    s.run('UPDATE operation SET result=? WHERE token=?', [JSON.stringify(result), token]);
    s.touch(true);
    return result;
  });
}
export function undoArchive(s: Store, operation: string, token: string) {
  return s.transaction(() => {
    const previous = previousOperation(s, token, 'undo', operation); if (previous) return previous;
    const op = s.one<{ expires_at: number; target: string; kind: string }>('SELECT * FROM operation WHERE token=?', [operation]);
    if (!op || op.kind !== 'archive') throw new AppError('归档操作不存在。', 404);
    if (op.expires_at < Date.now()) throw new AppError('撤销期限已过。', 409);
    const changed = s.run('UPDATE paper_state SET archived_at=NULL,archive_operation=NULL,version=version+1 WHERE archive_operation=? AND paper_id IN(SELECT paper_id FROM archive_item WHERE operation_token=?)', [operation, operation]).changes;
    // Rebuild affected projection in bounded worker batches.
    refreshJournal(s, op.target);
    const result = { count: Number(changed), operationId: token };
    s.run('INSERT INTO operation VALUES(?,?,?,?,?,NULL)', [token, 'undo', operation, JSON.stringify(result), Date.now()]);
    s.touch(true);
    return result;
  });
}
type ListRow = { id: string; journalId: string; journalName: string; titleOriginal: string; titleZh: string | null; published: string | null; datePrecision: string; favorite: number; archived: number; aiStatus: string; url: string; published_sort: string };
type Cursor = { v: number; query: string; sort: string; id: string; journal?: string; ceiling: string };
const groupCountsCache = new WeakMap<Store, { version: number; ceiling: string; groups: { journal_id: string; n: number }[] }>();
export function listPapers(s: Store, options: { journalId?: string; favorites?: boolean; cursor?: string; limit: number; page?: number }) {
  const grouped = !options.favorites && !options.journalId;
  const query = options.favorites ? 'favorites' : `inbox:${options.journalId || ''}`;
  const c = options.cursor ? decodeCursor<Cursor>(options.cursor) : null;
  if (c && (c.v !== (grouped ? 2 : 1) || c.query !== query || !/^\d+$/.test(c.id) || !/^\d+$/.test(c.ceiling))) throw new AppError('分页范围已改变，请重新加载。');
  const ceiling = c?.ceiling || String(s.one<{ id: number }>('SELECT COALESCE(MAX(id),0) AS id FROM paper')!.id);
  const params: (string | number)[] = options.favorites || !options.journalId ? [ceiling] : [options.journalId, ceiling];
  const condition = options.favorites ? 'l.favorited=1' : options.journalId ? 'l.inbox_visible=1 AND l.journal_id=?' : 'l.inbox_visible=1';
  const contentVersion = s.installation().content_version;
  let cachedGroups = groupCountsCache.get(s);
  if (grouped && (!cachedGroups || cachedGroups.version !== contentVersion || cachedGroups.ceiling !== ceiling)) {
    cachedGroups = { version: contentVersion, ceiling, groups: s.all<{ journal_id: string; n: number }>('SELECT CAST(journal_id AS TEXT) journal_id,COUNT(*) n FROM paper_listing WHERE inbox_visible=1 AND paper_id<=? GROUP BY journal_id ORDER BY journal_id', [ceiling]) };
    groupCountsCache.set(s, cachedGroups);
  }
  const total = grouped ? cachedGroups!.groups.reduce((sum, row) => sum + row.n, 0) : s.one<{ n: number }>(`SELECT COUNT(*) n FROM paper_listing l WHERE ${condition} AND l.paper_id<=?`, params)!.n;
  const totalPages = Math.max(1, Math.ceil(total / options.limit));
  const page = Math.min(options.page || 1, totalPages);
  let anchor = c ? { published_sort: c.sort, paper_id: c.id, journal_id: c.journal || '' } : null;
  const order = `${grouped ? 'l.journal_id ASC,' : ''}l.published_sort DESC,l.paper_id DESC`;
  const seek = () => !anchor ? '' : grouped ? 'AND l.journal_id>=? AND (l.journal_id>? OR (l.journal_id=? AND (l.published_sort<? OR (l.published_sort=? AND l.paper_id<?))))' : 'AND (l.published_sort<? OR (l.published_sort=? AND l.paper_id<?)))';
  const seekValues = () => !anchor ? [] : [...(grouped ? [anchor.journal_id, anchor.journal_id, anchor.journal_id] : []), anchor.published_sort, anchor.published_sort, anchor.paper_id];
  if (options.page && page > 1) {
    let remaining = (page - 1) * options.limit;
    if (grouped) {
      // Skip complete journals in one covering-index pass, then seek within one journal.
      const groups = cachedGroups!.groups;
      for (const group of groups) {
        if (remaining >= group.n) { remaining -= group.n; continue; }
        anchor = { journal_id: group.journal_id, published_sort: '\uffff', paper_id: '9223372036854775807' };
        break;
      }
    }
    // Walk the covering index in bounded batches, never load skipped titles.
    while (remaining > 0) {
      const seekParams = [...params];
      seekParams.push(...seekValues());
      const take = Math.min(1000, remaining); seekParams.push(take);
      const keys: { published_sort: string; paper_id: string; journal_id: string }[] = s.all(`SELECT published_sort,CAST(paper_id AS TEXT) paper_id,CAST(journal_id AS TEXT) journal_id FROM paper_listing l WHERE ${condition} AND l.paper_id<=? ${seek()} ORDER BY ${order} LIMIT ?`, seekParams);
      if (!keys.length) break;
      anchor = keys.at(-1)!; remaining -= keys.length;
    }
  }
  params.push(...seekValues());
  params.push(Math.min(100, Math.max(1, options.limit)) + 1);
  const rows = s.all<ListRow>(`SELECT CAST(p.id AS TEXT) AS id,CAST(p.journal_id AS TEXT) AS journalId,j.name AS journalName,p.title_original AS titleOriginal,l.title_zh AS titleZh,p.published,p.date_precision AS datePrecision,l.favorited AS favorite,ps.archived_at IS NOT NULL AS archived,l.ai_status AS aiStatus,p.url,l.published_sort FROM paper_listing l JOIN paper p ON p.id=l.paper_id JOIN paper_state ps ON ps.paper_id=p.id JOIN journal j ON j.id=l.journal_id WHERE ${condition} AND l.paper_id<=? ${seek()} ORDER BY ${order} LIMIT ?`, params);
  const items: Record<string, unknown>[] = []; let hasMore = rows.length > options.limit;
  let last: ListRow | undefined;
  for (const row of rows.slice(0, options.limit)) {
    const item = { id: row.id, journalId: row.journalId, journalName: row.journalName.slice(0, 300), titleOriginal: row.titleOriginal.slice(0, 400), titleZh: row.titleZh?.slice(0, 400) || null, titleTruncated: row.titleOriginal.length > 400 || (row.titleZh?.length || 0) > 400, published: row.published, datePrecision: row.datePrecision, favorite: !!row.favorite, archived: !!row.archived, aiStatus: row.aiStatus, url: row.url };
    items.push(item); last = row;
  }
  const journalIds = [...new Set(items.map(item => String(item.journalId)))];
  const journals = journalIds.length ? s.all<{ id: string; name: string; metadata: string }>(`SELECT CAST(id AS TEXT) id,name,metadata FROM journal WHERE id IN (${journalIds.map(() => '?').join(',')})`, journalIds).map(row => {
    const info = JSON.parse(row.metadata);
    return { id: row.id, name: row.name, zone: info.zone?.zone ?? null, top: info.top ?? null, ifDisplay: info.ifDisplay ?? null, category: info.category ?? null };
  }) : [];
  return { journals, items, page, total, totalPages, hasMore, nextCursor: hasMore && last ? encodeCursor({ v: grouped ? 2 : 1, query, sort: last.published_sort, id: last.id, journal: last.journalId, ceiling }) : null, asOfSeq: ceiling, viewVersion: s.installation().content_version };
}
