import { join } from 'node:path';
import { config } from '../lib/config.ts';
import { createBackup } from '../lib/backup.ts';
const c = config();
console.log(await createBackup(c.database, process.argv[2] || join(c.data, 'backups')));
