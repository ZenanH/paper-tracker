export const TIMEZONE = 'Asia/Shanghai';
const formatter = new Intl.DateTimeFormat('en-CA', { timeZone: TIMEZONE, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23' });

export function beijingParts(at: number) {
  const parts = Object.fromEntries(formatter.formatToParts(at).map(p => [p.type, p.value]));
  return { year: +parts.year, month: +parts.month, day: +parts.day, hour: +parts.hour, minute: +parts.minute, second: +parts.second };
}
export function beijingDate(at: number): string {
  const p = beijingParts(at);
  return `${p.year}-${String(p.month).padStart(2, '0')}-${String(p.day).padStart(2, '0')}`;
}
export function monthStart(at: number): number {
  const p = beijingParts(at);
  return Date.UTC(p.year, p.month - 1, 1) - 8 * 3600_000;
}
export function subtractMonths(at: number, months: number): number {
  const p = beijingParts(at);
  const d = new Date(Date.UTC(p.year, p.month - 1 - months, 1));
  const days = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 1, 0)).getUTCDate();
  return Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), Math.min(p.day, days), p.hour, p.minute, p.second, ((at % 1000) + 1000) % 1000) - 8 * 3600_000;
}
export function syncWindow(deploymentStart: number, initial: boolean, now: number) {
  return { start: initial ? deploymentStart : Math.max(deploymentStart, subtractMonths(now, 3)), end: now };
}
export function nextScheduled(at: number, hour: number, minute: number): number {
  const p = beijingParts(at);
  const today = Date.UTC(p.year, p.month - 1, p.day, hour, minute) - 8 * 3600_000;
  return today > at ? today : today + 86400_000;
}
export function dateShards(start: number, end: number) {
  const result: { from: string; until: string }[] = [];
  let from = beijingDate(start);
  const last = beijingDate(end);
  while (from <= last) {
    const [y, m] = from.split('-').map(Number);
    const until = `${y}-${String(m).padStart(2, '0')}-${new Date(Date.UTC(y, m, 0)).getUTCDate()}`;
    result.push({ from, until: until < last ? until : last });
    from = new Date(Date.UTC(y, m, 1)).toISOString().slice(0, 10);
  }
  return result;
}

export type Publication = { value: string | null; precision: 'day' | 'month' | 'year' | 'unknown'; field: string | null; sort: string; eligible: boolean };
export function publication(record: Record<string, unknown>, from: string, until: string): Publication {
  for (const field of ['published-online', 'published-print', 'published', 'issued']) {
    const date = record[field] as { 'date-parts'?: number[][] } | undefined;
    const p = date?.['date-parts']?.[0];
    if (!p?.length || !Number.isInteger(p[0]) || p[0] < 1600 || p[0] > 9999) continue;
    const year = String(p[0]);
    if (p.length === 1) return { value: year, precision: 'year', field, sort: year, eligible: false };
    if (!Number.isInteger(p[1]) || p[1] < 1 || p[1] > 12) continue;
    const month = `${year}-${String(p[1]).padStart(2, '0')}`;
    const days = new Date(Date.UTC(p[0], p[1], 0)).getUTCDate();
    if (p.length === 2) return { value: month, precision: 'month', field, sort: `${month}-01`, eligible: `${month}-01` <= until && `${month}-${days}` >= from };
    if (!Number.isInteger(p[2]) || p[2] < 1 || p[2] > days) continue;
    const value = `${month}-${String(p[2]).padStart(2, '0')}`;
    return { value, precision: 'day', field, sort: value, eligible: value >= from && value <= until };
  }
  return { value: null, precision: 'unknown', field: null, sort: '', eligible: false };
}
