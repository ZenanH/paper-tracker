import { setTimeout as delay } from 'node:timers/promises';

/** Refill each free slot independently; a slow sibling never creates a batch barrier. */
export async function runPool(options: {
  slots: number;
  capacity: () => number;
  work: (slot: number, signal: AbortSignal) => Promise<boolean>;
  signal: AbortSignal;
  idleMs?: number;
}) {
  const controller = new AbortController();
  const signal = AbortSignal.any([options.signal, controller.signal]);
  const lanes = Array.from({ length: options.slots }, (_, slot) => (async () => {
    try {
      while (!signal.aborted) {
        const worked = slot < options.capacity() && await options.work(slot, signal);
        await delay(worked ? 0 : options.idleMs ?? 1000, undefined, { signal }).catch(() => {});
      }
    } catch (error) { controller.abort(); throw error; }
  })());
  const results = await Promise.allSettled(lanes);
  const failure = results.find(result => result.status === 'rejected');
  if (failure?.status === 'rejected') throw failure.reason;
}
