// Check the exact Git candidate set, including before this directory has been initialized.
import assert from 'node:assert/strict';
import { execFileSync, spawnSync } from 'node:child_process';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
const root = resolve('.');
const probe = spawnSync('git', ['rev-parse', '--show-toplevel'], { encoding: 'utf8' });
let temporary;
try {
  let args = [];
  if (probe.status !== 0 || resolve(probe.stdout.trim()) !== root) {
    temporary = mkdtempSync(join(tmpdir(), 'paper-repository-'));
    execFileSync('git', ['init', '--quiet', temporary]);
    args = ['--git-dir=' + join(temporary, '.git'), '--work-tree=' + root];
  }
  const files = execFileSync('git', [...args, 'ls-files', '--cached', '--others', '--exclude-standard', '-z'], { encoding: 'utf8' }).split('\0').filter(Boolean);
  const forbidden = /(^|\/)(?:\.local(?:[^/]*)|node_modules|\.next|dist|catalog|\.pnpm-store|paper-tracker-data|backups|secrets|test-results|playwright-report)(?:\/|$)|(?:\.sqlite(?:-[\w]+)?|\.db|\.log|\.tsbuildinfo|\.tar|\.pem|\.key)$|(?:^|\/)(?:compose\.env|encryption_key|session_secret|app_password|\.env(?:\..*)?)$/;
  for (const file of files) {
    if (file === '.env.example') continue;
    assert.ok(!forbidden.test(file), 'Local state/build artifact in upload set: ' + file);
    const text = readFileSync(join(root, file));
    assert.ok(!/(?:sk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{32,}|gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{50,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)/.test(text.toString('utf8')), 'Possible credential in upload set: ' + file);
  }
  for (const file of ['pnpm-lock.yaml', '.github/workflows/ci.yml', 'Dockerfile', 'compose.yaml', 'NOTICE.md', 'data/journals/manifest.json', 'third_party/ShowJCR-LICENSE.txt', 'public/fonts/inter-LICENSE.txt', 'public/fonts/noto-LICENSE.txt']) assert.ok(files.includes(file), 'Missing repository file: ' + file);
  const pkg = JSON.parse(readFileSync('package.json', 'utf8'));
  assert.match(pkg.version, /^1\.\d+\.0$/); assert.equal(pkg.private, true);
  assert.match(pkg.packageManager, /^pnpm@\d+\.\d+\.\d+$/);
  console.log(`Repository check passed: ${files.length} source, test, documentation and asset files; no instance state or build artifacts in upload set.`);
} finally { if (temporary) rmSync(temporary, { recursive: true, force: true }); }
