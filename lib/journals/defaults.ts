import defaults from '../../data/journals/default-follows.json' with { type: 'json' };
import type { Store } from '../db/index.ts';
import { searchCatalog } from './catalog.ts';
import { followJournal } from './service.ts';
import { enqueueSync } from '../sync.ts';

// Called inside the installation transaction, exclusively for a new instance.
// Resolve the entire list before writing; never accept partial or ambiguous seeds.
export function seedDefaultJournals(s: Store) {
  const journals = defaults.journals.map(entry => {
    const matches = new Map(entry.issns.flatMap(issn => searchCatalog(issn)).map(info => [info.id, info]));
    if (matches.size !== 1) throw new Error(`默认关注期刊的刊号未唯一匹配：${entry.name}`);
    const info = [...matches.values()][0];
    if (!entry.issns.every(issn => info.issns.includes(issn))) throw new Error(`默认关注期刊刊号发生变化：${entry.name}`);
    return info;
  });
  if (new Set(journals.map(j => j.id)).size !== journals.length) throw new Error('默认关注列表含重复期刊。');
  const ids = journals.map(info => followJournal(s, info, false).id);
  if (new Set(ids).size !== ids.length) throw new Error('Duplicate follow IDs: ' + ids.join(','));
  // One fixed initial target snapshot includes all default journals.
  enqueueSync(s, ids, undefined, s.installation().installed_at);
}
