import { DatabaseSync } from 'node:sqlite';
import { readFileSync, mkdirSync, renameSync, rmSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { createHash } from 'node:crypto';
import { pathToFileURL } from 'node:url';
import { parse } from 'csv-parse/sync';
import { normalizeName, splitISSNs, validISSN, parseZone, type JournalInfo } from '../lib/journals/catalog.ts';

type Row = Record<string, string>;
export function buildCatalog(output = resolve('catalog/catalog.sqlite')) {
  const manifest = JSON.parse(readFileSync('data/journals/manifest.json', 'utf8'));
  const tables: Row[][] = [];
  for (const spec of manifest.files) {
    const raw = readFileSync(spec.path);
    if (createHash('sha256').update(raw).digest('hex') !== spec.sha256) throw new Error(`期刊快照校验失败：${spec.path}`);
    const rows = parse(raw, { columns: true, bom: true, skip_empty_lines: true }) as Row[];
    if (rows.length !== spec.records || JSON.stringify(Object.keys(rows[0])) !== JSON.stringify(spec.columns)) throw new Error('期刊快照字段或条数不符。');
    tables.push(rows);
  }
  const [cas, jcr] = tables;
  const jcrISSN = new Map<string, Set<number>>();
  jcr.forEach((r, i) => splitISSNs(`${r.ISSN}/${r.EISSN}`).forEach(issn => { if (!jcrISSN.has(issn)) jcrISSN.set(issn, new Set()); jcrISSN.get(issn)!.add(i); }));
  const combined = new Map<number, Row>();
  const unmatched: Row[] = [];
  for (const r of cas) {
    const matches = new Set(splitISSNs(r['ISSN/EISSN']).flatMap(i => [...(jcrISSN.get(i) || [])]));
    if (matches.size > 1) throw new Error('存在多义 ISSN 关联，停止导入。');
    if (matches.size === 1) {
      const key = [...matches][0];
      if (combined.has(key)) throw new Error('多条分区记录指向同一期刊，需要人工核实。');
      combined.set(key, r);
    } else unmatched.push(r);
  }
  mkdirSync(dirname(output), { recursive: true });
  const temp = `${output}.partial`;
  rmSync(temp, { force: true });
  const db = new DatabaseSync(temp);
  db.exec(`PRAGMA journal_mode=DELETE; CREATE TABLE catalog(id INTEGER PRIMARY KEY,name TEXT NOT NULL,normalized_name TEXT NOT NULL,metadata TEXT NOT NULL); CREATE INDEX catalog_name ON catalog(normalized_name); CREATE TABLE catalog_issn(issn TEXT NOT NULL,catalog_id INTEGER NOT NULL,PRIMARY KEY(issn,catalog_id)); CREATE VIRTUAL TABLE catalog_search USING fts5(name,aliases,tokenize='unicode61 remove_diacritics 2'); CREATE TABLE provenance(manifest TEXT NOT NULL); BEGIN;`);
  const insert = db.prepare('INSERT INTO catalog VALUES(?,?,?,?)');
  const issnInsert = db.prepare('INSERT INTO catalog_issn VALUES(?,?)');
  const searchInsert = db.prepare('INSERT INTO catalog_search(rowid,name,aliases) VALUES(?,?,?)');
  const write = (id: number, j: Row | null, c: Row | null) => {
    const issns = [...new Set([...splitISSNs(c?.['ISSN/EISSN'] || ''), ...splitISSNs(`${j?.ISSN || ''}/${j?.EISSN || ''}`)])];
    const name = c?.Journal || j!.Journal;
    const ifDisplay = j?.['IF(2025)'] || null;
    const value = ifDisplay && ifDisplay !== 'N/A' ? Number(ifDisplay.replace(/^</, '')) : null;
    const info: JournalInfo = {
      id: String(id), name, aliases: j && j.Journal !== name ? [j.Journal] : [], issns,
      category: c?.['大类'] || null, zone: c ? parseZone(c['大类分区']) : null, top: c ? c.Top === '是' : null,
      subjects: c ? Array.from({ length: 6 }, (_, i) => ({ name: c[`小类${i + 1}`].trim(), zone: parseZone(c[`小类${i + 1}分区`]) })).filter(s => s.name) : [],
      ifDisplay, ifValue: value !== null && Number.isFinite(value) ? value : null,
      ifOperator: value === null ? null : ifDisplay!.startsWith('<') ? '<' : '=',
      casYear: c ? 2025 : null, ifYear: j ? 2025 : null, ifReleaseYear: j ? 2026 : null,
      source: `ShowJCR@${manifest.commit}`, invalidIssns: issns.filter(i => !validISSN(i))
    };
    insert.run(id, name, normalizeName(name), JSON.stringify(info));
    for (const issn of issns) issnInsert.run(issn, id);
    searchInsert.run(id, normalizeName(name), info.aliases.map(normalizeName).join(' '));
  };
  try {
    jcr.forEach((row, i) => write(i + 1, row, combined.get(i) || null));
    unmatched.forEach((row, i) => write(jcr.length + i + 1, null, row));
    db.prepare('INSERT INTO provenance VALUES(?)').run(JSON.stringify(manifest));
    db.exec('COMMIT; ANALYZE; VACUUM;');
  } finally { db.close(); }
  renameSync(temp, output);
  console.log(`内置期刊目录已生成：${jcr.length + unmatched.length} 本；来源校验通过。`);
}
if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) buildCatalog();
