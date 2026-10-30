import type { Store } from './db/index.ts';
import { AppError } from './security.ts';

export function updateRunning(s: Store) {
  return !!s.one("SELECT id FROM task WHERE state IN ('queued','running','retry') LIMIT 1");
}
export function requireUpdateIdle(s: Store) {
  if (updateRunning(s)) throw new AppError('Update in progress. Please wait.', 409, 'update_in_progress');
}
