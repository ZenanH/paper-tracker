import { spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import { config } from '../lib/config.ts';
if (!existsSync(config().database)) { console.error('请先运行 pnpm setup。'); process.exit(1); }
const processes = [
  spawn(process.execPath, ['node_modules/next/dist/bin/next', 'dev', '--hostname', '127.0.0.1'], { stdio: 'inherit', env: process.env }),
  spawn(process.execPath, ['--import', 'tsx', 'worker/index.ts'], { stdio: 'inherit', env: process.env })
];
let stopped = false;
function stop(code = 0) { if (stopped) return; stopped = true; for (const child of processes) child.kill('SIGTERM'); process.exitCode = code; }
for (const signal of ['SIGINT', 'SIGTERM'] as const) process.on(signal, () => stop());
for (const child of processes) child.on('exit', code => stop(code || 0));
