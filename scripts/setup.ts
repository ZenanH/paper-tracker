import { seedDefaultJournals } from '../lib/journals/defaults.ts';
import { existsSync, mkdirSync, writeFileSync, chmodSync } from 'node:fs';
import { join } from 'node:path';
import { randomBytes } from 'node:crypto';
import { config, secret } from '../lib/config.ts';
import { bootstrap } from '../lib/db/index.ts';
import { buildCatalog } from './build-catalog.ts';

const c = config();
const existing = existsSync(c.database);
mkdirSync(c.data, { recursive: true, mode: 0o700 });
mkdirSync(join(c.home, 'secrets'), { recursive: true, mode: 0o700 });
for (const file of [c.sessionFile, c.encryptionFile]) {
  if (!existsSync(file)) {
    if (existing) throw new Error('已有实例缺少密钥，停止初始化。请恢复原有 secret 文件。');
    writeFileSync(file, randomBytes(32).toString('hex') + '\n', { mode: 0o600, flag: 'wx' });
  }
  secret(file);
  if (process.platform !== 'win32') chmodSync(file, 0o600);
}
if (!existsSync(c.catalog)) buildCatalog(c.catalog);
const s = bootstrap(c.database, Date.now(), seedDefaultJournals);
console.log(`本地实例已就绪（${s.installation().instance_id}）。`);
console.log('AI 不需要预先配置；启动 pnpm dev 后可在设置页填写。');
s.close();
