import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync, writeFileSync, readFileSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import { randomBytes } from 'node:crypto';
import { bootstrap } from '../lib/db/index.ts';
import { createBackup, restoreBackup, BLOCK_SIZE } from '../lib/backup.ts';
import { encrypt, decrypt } from '../lib/security.ts';

test('在线 WAL 备份分块可恢复，保留安装/用户状态/凭据，损坏或丢块拒绝发布', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'paper-backup-')), key = join(dir, 'key');
  const previous = process.env.ENCRYPTION_KEY_FILE;
  process.env.ENCRYPTION_KEY_FILE = key;
  writeFileSync(key, randomBytes(32).toString('hex'), { mode: 0o600 });
  const path = join(dir, 'db.sqlite'), s = bootstrap(path, 1793200000000);
  try {
    s.run("INSERT INTO journal VALUES(1,NULL,'Test','{}')");
    s.run("INSERT INTO paper VALUES(1,'10.1/test',1,'Original title','hash','hash','2026-10-01','day','published','2026-10-01',1,'https://example.com','test',1,1)");
    s.run('INSERT INTO paper_state(paper_id,archived_at,favorited_at) VALUES(1,12,34)');
    s.run('UPDATE ai_settings SET encrypted_key=? WHERE id=1', [encrypt('test-only-key')]);
    s.run('UPDATE installation SET worker_heartbeat_at=123 WHERE id=1');
    // Exceeds one raw block, while live connection holds uncheckpointed WAL changes.
    s.db.exec('PRAGMA wal_autocheckpoint=0; CREATE TABLE backup_test_payload(bytes BLOB)');
    s.run('INSERT INTO backup_test_payload VALUES(?)', [randomBytes(BLOCK_SIZE + 1000)]);
    const before = s.installation(), snapshot = await createBackup(path, join(dir, 'backups'));
    const manifest = JSON.parse(readFileSync(join(snapshot, 'manifest.json'), 'utf8'));
    assert.ok(manifest.parts.length >= 2);
    assert.ok(!readFileSync(join(snapshot, 'manifest.json'), 'utf8').includes('test-only-key'));
    const restored = restoreBackup(snapshot, join(dir, 'restored'));
    const db = new DatabaseSync(restored, { readOnly: true });
    try {
      assert.equal(db.prepare('SELECT installed_at FROM installation').get()!.installed_at, before.installed_at);
      assert.equal(db.prepare('SELECT instance_id FROM installation').get()!.instance_id, before.instance_id);
      assert.equal(db.prepare('SELECT worker_heartbeat_at FROM installation').get()!.worker_heartbeat_at, null);
      assert.deepEqual({ ...db.prepare('SELECT archived_at,favorited_at FROM paper_state').get() }, { archived_at: 12, favorited_at: 34 });
      assert.equal(decrypt(String(db.prepare('SELECT encrypted_key FROM ai_settings').get()!.encrypted_key)), 'test-only-key');
      assert.equal(db.prepare('SELECT length(bytes) n FROM backup_test_payload').get()!.n, BLOCK_SIZE + 1000);
    } finally { db.close(); }
    assert.throws(() => restoreBackup(snapshot, join(dir, 'restored')), /EEXIST/);
    writeFileSync(key, randomBytes(32).toString('hex'));
    assert.throws(() => restoreBackup(snapshot, join(dir, 'wrong-key')), /密钥无法解密/);
    assert.equal(existsSync(join(dir, 'wrong-key')), false);
    const part = join(snapshot, manifest.parts[0].file), original = readFileSync(part);
    const corrupt = Buffer.from(original); corrupt[50] ^= 1; writeFileSync(part, corrupt);
    assert.throws(() => restoreBackup(snapshot, join(dir, 'corrupt')), /校验失败/);
    assert.equal(existsSync(join(dir, 'corrupt')), false);
    rmSync(part);
    assert.throws(() => restoreBackup(snapshot, join(dir, 'missing')), /ENOENT/);
    writeFileSync(part, original); // Same snapshot resumes by replacing only the damaged/missing block.
    assert.equal(s.installation().installed_at, before.installed_at);
  } finally {
    s.close(); rmSync(dir, { recursive: true, force: true });
    if (previous === undefined) delete process.env.ENCRYPTION_KEY_FILE; else process.env.ENCRYPTION_KEY_FILE = previous;
  }
});
