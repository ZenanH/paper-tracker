import { test } from 'node:test';
import assert from 'node:assert/strict';
import { setTimeout as delay } from 'node:timers/promises';
import { runPool } from '../worker/pool.ts';

test('慢请求不阻塞空闲槽补充任务；并发上限与取消保持有效', async () => {
  const controller = new AbortController();
  let next = 0, active = 0, peak = 0;
  const completed: number[] = [];
  await runPool({ slots: 3, capacity: () => 3, signal: controller.signal, idleMs: 1,
    work: async (_slot, signal) => {
      const id = next++;
      active++; peak = Math.max(peak, active);
      try { await delay(id === 0 ? 150 : 5, undefined, { signal }); completed.push(id); }
      catch { /* Abort is expected for the intentionally slow first request. */ }
      finally { active--; }
      if (completed.length >= 12) controller.abort();
      return true;
    }
  });
  assert.equal(peak, 3);
  assert.ok(completed.length >= 12);
  assert.ok(!completed.includes(0), '快任务应在慢任务完成前持续推进');
  assert.equal(active, 0);
});

test('池任务异常会取消其他槽并等待清理，避免关闭数据库后继续提交', async () => {
  let cleaned = false;
  await assert.rejects(runPool({ slots: 2, capacity: () => 2, signal: new AbortController().signal,
    work: async (slot, signal) => {
      if (slot === 0) { await delay(10); throw new Error('simulated failure'); }
      try { await delay(1000, undefined, { signal }); } finally { cleaned = true; }
      return false;
    }
  }), /simulated failure/);
  assert.equal(cleaned, true);
});
