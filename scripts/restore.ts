import { restoreBackup } from '../lib/backup.ts';
const [source, destination] = process.argv.slice(2);
if (!source || !destination) throw new Error('用法：restore <备份快照目录> <尚不存在的新数据目录>；通过 ENCRYPTION_KEY_FILE 指向原实例密钥。');
console.log(restoreBackup(source, destination));
