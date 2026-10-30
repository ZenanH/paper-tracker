import { seedDefaultJournals } from '../lib/journals/defaults.ts';
import { config, secret } from '../lib/config.ts';
import { bootstrap } from '../lib/db/index.ts';
const c = config();
for (const file of [c.sessionFile, c.encryptionFile]) secret(file);
const db = bootstrap(c.database, Date.now(), seedDefaultJournals);
db.close();
console.log('数据库迁移与安装基准初始化完成。');
