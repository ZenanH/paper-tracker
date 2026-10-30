import { store } from '../db/index.ts';
import { z } from 'zod';
import { config } from '../config.ts';
import { APP_VERSION } from '../version.ts';
import { boundedJSON, ExternalError, retryAfter } from '../network.ts';
import { publication } from '../time.ts';
import { normalizeDOI, type PaperInput } from '../papers.ts';
import { validISSN, type JournalInfo } from '../journals/catalog.ts';

const itemSchema = z.object({ DOI: z.string().min(1).max(2048), title: z.array(z.string().max(32000)).min(1), ISSN: z.array(z.string()).optional(), URL: z.string().max(4096).optional(), type: z.string().optional(), 'published-online': z.unknown().optional(), 'published-print': z.unknown().optional(), published: z.unknown().optional(), issued: z.unknown().optional() });
// Adapter constructs a fresh allowlisted object; abstract/author/reference never enter application data.
export function cleanTitle(value: string) {
  return value.replace(/<[^>]*>/g, ' ').replace(/&(?:amp|lt|gt|quot|apos|nbsp);/g, e => ({ '&amp;': '&', '&lt;': '<', '&gt;': '>', '&quot;': '"', '&apos;': "'", '&nbsp;': ' ' })[e] || e).replace(/&#(x[0-9a-f]+|\d+);/gi, (_, n: string) => { const point = n[0].toLowerCase() === 'x' ? parseInt(n.slice(1), 16) : +n; return point > 0 && point <= 0x10ffff ? String.fromCodePoint(point) : ''; }).replace(/[\u0000-\u001f]/g, ' ').replace(/\s+/g, ' ').trim();
}
export function adaptWork(raw: unknown, issns: string[], from: string, until: string): PaperInput | null {
  const result = itemSchema.safeParse(raw);
  if (!result.success) return null;
  const item = result.data;
  if (item.type && item.type !== 'journal-article') return null;
  if (!item.ISSN?.some(i => issns.includes(i.toUpperCase()))) return null;
  const doi = normalizeDOI(item.DOI);
  if (!/^10\.\d{4,9}\/\S+$/i.test(doi)) return null;
  const title = cleanTitle(item.title[0]); if (!title) return null;
  const date = publication(item, from, until);
  let url = `https://doi.org/${doi}`;
  if (item.URL) { try { const parsed = new URL(item.URL); if (['https:', 'http:'].includes(parsed.protocol) && !parsed.username && !parsed.password) url = parsed.href; } catch { /* DOI link is canonical fallback. */ } }
  return { doi, title, url, date, source: 'crossref' };
}
let nextRequestAt = 0;
let rateInterval = 1000;
let permittedConcurrency = 1, cooldownUntil = 0;
let startGate: Promise<void> = Promise.resolve();
export function crossrefConcurrency() { return Date.now() < cooldownUntil ? 1 : permittedConcurrency; }
export async function crossref(url: URL, signal?: AbortSignal) {
  if (url.origin !== 'https://api.crossref.org') throw new Error('Invalid Crossref origin');
  // Serialize admission, not HTTP responses: simultaneous tasks share one rate clock.
  const admission = startGate.then(async () => {
    while (Date.now() < Math.max(nextRequestAt, cooldownUntil)) {
      const wait = Math.max(nextRequestAt, cooldownUntil) - Date.now();
      await new Promise<void>(resolve => setTimeout(resolve, Math.min(wait, 1000)));
      if (signal?.aborted) throw new ExternalError('request_cancelled', true);
    }
    if (signal?.aborted) throw new ExternalError('request_cancelled', true);
    nextRequestAt = Date.now() + rateInterval;
  });
  startGate = admission.catch(() => {});
  await admission;
  const mailto = store().one<{ crossref_email: string }>('SELECT crossref_email FROM source_settings WHERE id=1')?.crossref_email ?? config().mailto;
  if (mailto && /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(mailto)) url.searchParams.set('mailto', mailto);
  let response: Response;
  try { response = await fetch(url, { headers: { 'user-agent': `paper-tracker/${APP_VERSION}`, accept: 'application/json', 'accept-encoding': 'gzip' }, signal: signal ? AbortSignal.any([signal, AbortSignal.timeout(40_000)]) : AbortSignal.timeout(40_000), redirect: 'error' }); }
  catch { throw new ExternalError('crossref_connection_failed', true); }
  const limit = Number(response.headers.get('x-rate-limit-limit')), interval = parseFloat(response.headers.get('x-rate-limit-interval') || '1');
  if (limit > 0 && interval > 0) rateInterval = Math.max(1000, interval * 1000 / limit);
  const concurrency = Number(response.headers.get('x-concurrency-limit'));
  permittedConcurrency = mailto && Number.isFinite(concurrency) && concurrency >= 1 ? Math.min(3, Math.floor(concurrency)) : 1;
  if (response.status === 429) {
    permittedConcurrency = 1;
    cooldownUntil = Math.max(cooldownUntil, retryAfter(response.headers.get('retry-after')) || Date.now() + 60_000);
    rateInterval = Math.max(2000, rateInterval * 2);
  }
  if (!response.ok) {
    await response.body?.cancel();
    throw new ExternalError(response.status === 429 ? 'crossref_rate_limited' : `crossref_http_${response.status}`, response.status === 429 || response.status >= 500, retryAfter(response.headers.get('retry-after')));
  }
  return boundedJSON(response);
}
export async function verifyJournal(issn: string, signal?: AbortSignal): Promise<JournalInfo> {
  if (!validISSN(issn)) throw new ExternalError('invalid_issn', false);
  const { data } = await crossref(new URL(`https://api.crossref.org/journals/${encodeURIComponent(issn)}`), signal);
  const result = z.object({ message: z.object({ title: z.string().min(1).max(1000), ISSN: z.array(z.string()).min(1) }) }).safeParse(data);
  if (!result.success || !result.data.message.ISSN.includes(issn)) throw new ExternalError('crossref_journal_invalid', false);
  return { id: `crossref:${issn}`, name: cleanTitle(result.data.message.title), aliases: [], issns: [...new Set(result.data.message.ISSN.filter(validISSN))], category: null, zone: null, top: null, subjects: [], ifDisplay: null, ifValue: null, ifOperator: null, casYear: null, ifYear: null, ifReleaseYear: null, source: 'crossref', invalidIssns: [] };
}
export type FetchStep = { issn: string; from: string; until: string; mode: 'pub' | 'online'; endpoint?: 'works' };
export async function fetchPage(step: FetchStep, cursor: string, rows: number, signal?: AbortSignal) {
  const cachedRoute = store().one<{ checked_at: number }>('SELECT checked_at FROM crossref_routes WHERE issn=?', [step.issn]);
  const useWorks = step.endpoint === 'works' || !!(cachedRoute && Date.now() - cachedRoute.checked_at < 7 * 86400_000);
  const started = performance.now();
  const url = new URL(useWorks ? 'https://api.crossref.org/works' : `https://api.crossref.org/journals/${encodeURIComponent(step.issn)}/works`);
  const dateType = step.mode === 'online' ? 'online-pub-date' : 'pub-date';
  url.searchParams.set('filter', `${useWorks ? `issn:${step.issn},` : ''}type:journal-article,from-${dateType}:${step.from},until-${dateType}:${step.until}`);
  url.searchParams.set('select', 'DOI,title,ISSN,published-online,published-print,published,issued,URL,type');
  url.searchParams.set('rows', String(rows)); url.searchParams.set('cursor', cursor);
  const result = await crossref(url, signal);
  const parsed = z.object({ message: z.object({ items: z.array(z.unknown()), 'next-cursor': z.string().optional(), 'total-results': z.number().optional() }) }).safeParse(result.data);
  if (!parsed.success) throw new ExternalError('crossref_invalid_response', true);
  if (step.endpoint === 'works') store().run('INSERT INTO crossref_routes(issn,checked_at) VALUES(?,?) ON CONFLICT(issn) DO UPDATE SET checked_at=excluded.checked_at', [step.issn, Date.now()]);
  console.info(JSON.stringify({ event: 'crossref_page', issn: step.issn, endpoint: useWorks ? 'works' : 'journal', durationMs: Math.round(performance.now() - started), bytes: result.bytes, items: parsed.data.message.items.length }));
  return { items: parsed.data.message.items, nextCursor: parsed.data.message['next-cursor'], bytes: result.bytes };
}
