import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { join, resolve } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
export const root = resolve(process.env.PAPER_TEST_ROOT || '.local/production-audit');
export const project = process.env.PAPER_TEST_PROJECT || 'paper-tracker-audit';
export const image = process.env.PAPER_TEST_IMAGE || 'paper-tracker:ci-test';
export const base = `http://localhost:${process.env.PAPER_TEST_PORT || '3012'}`;
export const web = `${project}-web-1`;
export const compose = ['compose', '--env-file', join(root, 'compose.env'), '-f', 'compose.yaml', '-f', 'tests/docker/override.yaml', '-p', project];
export const resultFile = join(root, 'results.json');
export const docker = (...args) => execFileSync('docker', args, { encoding: 'utf8', timeout: 180_000, env: { ...process.env, PAPER_TEST_IMAGE: image } });
export const db = code => JSON.parse(docker('exec', web, 'node', '--input-type=module', '-e', `import {store} from './dist/lib/db/index.js';const s=store();try {console.log(JSON.stringify(${code}))}finally{s.close()}`));
export async function call(path, body) {
  const response = await fetch(base + '/api' + path, { method: body === undefined ? 'GET' : 'POST', headers: { origin: base, 'content-type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body), signal: AbortSignal.timeout(90_000) });
  const value = await response.json(); assert.ok(response.ok, JSON.stringify(value)); return value;
}
export async function wait(fn, label) {
  const end = Date.now() + 90_000;
  while (Date.now() < end) { if (await fn()) return; await delay(500); }
  throw new Error('Timeout: ' + label);
}
export const idle = () => wait(async () => !(await call('/status')).active, 'worker idle');
