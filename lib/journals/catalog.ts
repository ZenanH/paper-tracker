import { DatabaseSync } from 'node:sqlite';
import { existsSync } from 'node:fs';
import { config } from '../config.ts';

export type Zone = { zone: number | null; raw: string; rank: number | null; total: number | null };
export type JournalInfo = {
  id: string; name: string; aliases: string[]; issns: string[];
  category: string | null; zone: Zone | null; top: boolean | null;
  subjects: { name: string; zone: Zone }[];
  ifDisplay: string | null; ifValue: number | null; ifOperator: '<' | '=' | null;
  casYear: number | null; ifYear: number | null; ifReleaseYear: number | null;
  source: string; invalidIssns: string[];
};
export function normalizeName(name: string) { return name.normalize('NFKC').toLowerCase().replace(/[^\p{L}\p{N}]+/gu, ' ').trim(); }
export function validISSN(issn: string) {
  if (!/^\d{4}-\d{3}[\dX]$/.test(issn)) return false;
  const digits = issn.replace('-', '');
  const sum = [...digits].reduce((total, d, i) => total + (d === 'X' ? 10 : +d) * (8 - i), 0);
  return sum % 11 === 0;
}
export function splitISSNs(value: string) {
  return [...new Set(value.toUpperCase().replace(/\bN\/A\b/g, '').split('/').map(s => s.trim()).filter(s => s && s !== 'N/A' && s !== '-'))];
}
export function parseZone(raw: string): Zone {
  const m = raw.match(/^([1-4])(?:\s*\[(\d+)\/(\d+)\])?/);
  return { zone: m ? +m[1] : null, raw, rank: m?.[2] ? +m[2] : null, total: m?.[3] ? +m[3] : null };
}
let cached: DatabaseSync | undefined;
function catalog() {
  if (!cached) {
    const path = config().catalog;
    if (!existsSync(path)) throw new Error('内置目录尚未生成，请先运行 pnpm catalog。');
    cached = new DatabaseSync(path, { readOnly: true });
  }
  return cached;
}
export function catalogJournal(id: string): JournalInfo | null {
  if (!/^\d+$/.test(id)) return null;
  const row = catalog().prepare('SELECT metadata FROM catalog WHERE id=?').get(id);
  return row ? JSON.parse(String(row.metadata)) as JournalInfo : null;
}
export function searchCatalog(query: string): JournalInfo[] {
  const text = query.trim().slice(0, 160);
  if (text.length < 2) return [];
  const c = catalog();
  const issn = text.toUpperCase().replace(/^(\d{4})(\d{3}[\dX])$/, '$1-$2');
  if (/^\d{4}-\d{3}[\dX]$/.test(issn)) {
    return c.prepare('SELECT DISTINCT c.metadata FROM catalog c JOIN catalog_issn i ON i.catalog_id=c.id WHERE i.issn=? LIMIT 20').all(issn).map(r => JSON.parse(String(r.metadata)));
  }
  const words = normalizeName(text).split(' ').filter(Boolean);
  if (!words.length) return [];
  const match = words.map(w => `"${w.replaceAll('"', '')}"*`).join(' AND ');
  return c.prepare('SELECT c.metadata FROM catalog_search s JOIN catalog c ON c.id=s.rowid WHERE catalog_search MATCH ? ORDER BY rank LIMIT 20').all(match).map(r => JSON.parse(String(r.metadata)));
}
