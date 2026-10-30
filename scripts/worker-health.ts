import { store } from '../lib/db/index.ts';
const s = store();
try {
  const heartbeat = s.installation().worker_heartbeat_at;
  if (!heartbeat || Date.now() - heartbeat > 25_000) process.exitCode = 1;
} finally { s.close(); }
