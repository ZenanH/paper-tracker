// A fresh Compose project per run; no user database, upstream network, or paid AI key.
import { mkdtempSync, mkdirSync, writeFileSync, chownSync, readFileSync, rmSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { randomBytes, randomUUID } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { createServer } from 'node:net';

mkdirSync('.local', { recursive: true });
const root = mkdtempSync(resolve('.local/ci-docker-'));
const uid = process.getuid?.() || 1000, gid = process.getuid?.() === 0 ? 1000 : process.getgid();
const port = process.env.PAPER_TEST_PORT || await new Promise((resolvePort, reject) => {
  const server = createServer(); server.on('error', reject);
  server.listen(0, '127.0.0.1', () => { const port = server.address().port; server.close(() => resolvePort(String(port))); });
});
Object.assign(process.env, { PAPER_TEST_ROOT: root, PAPER_TEST_PROJECT: 'paper-tracker-ci-' + randomUUID().slice(0, 8), PAPER_TEST_PORT: String(port) });
const { compose, docker, image, idle, resultFile } = await import('../tests/docker/context.mjs');
const instance = join(root, 'instance');
for (const dir of [instance, join(instance, 'data'), join(instance, 'secrets')]) mkdirSync(dir, { mode: 0o700 });
for (const name of ['session_secret', 'encryption_key']) writeFileSync(join(instance, 'secrets', name), randomBytes(32).toString('hex') + '\n', { mode: 0o600, flag: 'wx' });
if (process.getuid() === 0) for (const path of [root, instance, join(instance, 'data'), join(instance, 'secrets'), ...['session_secret', 'encryption_key'].map(name => join(instance, 'secrets', name))]) chownSync(path, uid, gid);
const env = { PAPER_TRACKER_DATA: instance, PUID: uid, PGID: gid, APP_URL: `http://localhost:${port}`, WEB_PORT: port, BIND_ADDRESS: '127.0.0.1', AI_ALLOWED_ORIGINS: 'http://audit-source:8099' };
writeFileSync(join(root, 'compose.env'), Object.entries(env).map(([k, v]) => k + '=' + JSON.stringify(String(v))).join('\n') + '\n', { mode: 0o600 });
const run = file => execFileSync(process.execPath, [file], { stdio: 'inherit', env: { ...process.env, PAPER_TEST_IMAGE: image }, timeout: 600_000 });
let started = false;
try {
  if (!process.argv.includes('--no-build')) execFileSync('docker', ['build', '-t', image, '.'], { stdio: 'inherit', timeout: 900_000 });
  started = true;
  console.log(docker(...compose, 'up', '-d', '--no-build', '--wait', '--wait-timeout', '120', 'web', 'audit-source'));
  run('tests/docker/configure.mjs');
  console.log(docker(...compose, 'up', '-d', '--no-build', '--wait', '--wait-timeout', '120', 'worker'));
  await idle();
  run('tests/docker/lifecycle.mjs');
  run('tests/docker/final-verify.mjs');
  console.log('Docker smoke test passed:', readFileSync(resultFile, 'utf8'));
} catch (error) {
  if (started) { try { console.error(docker(...compose, 'logs', '--no-color', '--tail=60')); } catch {} }
  throw error;
} finally {
  if (started) {
    console.log(docker(...compose, '-p', process.env.PAPER_TEST_PROJECT + '-failure', '-f', 'tests/docker/failure.yaml', 'down', '--remove-orphans'));
    console.log(docker(...compose, 'down', '--remove-orphans'));
  }
  if (process.env.PAPER_TEST_KEEP !== '1') rmSync(root, { recursive: true, force: true });
  else console.log('Test artifacts retained at', root);
}
