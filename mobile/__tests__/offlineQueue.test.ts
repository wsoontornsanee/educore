/**
 * Offline Attendance Queue Unit Tests (TCH-004).
 */
import { describe, it, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import {
  clearAllForTesting,
  enqueueAttendance,
  getPendingCount,
  getPendingQueue,
  syncPendingEntries,
} from '../src/services/offlineQueue.ts';
import { apiClient } from '../src/services/api.ts';
import type { PeriodAttendanceEntry } from '../src/types/index.ts';

describe('Offline Attendance Queue', () => {
  beforeEach(async () => {
    await clearAllForTesting();
  });

  it('enqueues attendance with unique UUID and idempotency key', async () => {
    const entries: PeriodAttendanceEntry[] = [
      { student_id: 101, status: 'HADIR' },
      { student_id: 102, status: 'ALPA' },
    ];

    const result = await enqueueAttendance(12, '2026-09-16', entries);

    assert.ok(result.id);
    assert.ok(result.idempotency_key);
    assert.strictEqual(result.item.slot_id, 12);
    assert.strictEqual(result.item.status, 'PENDING');

    const pending = await getPendingQueue();
    assert.strictEqual(pending.length, 1);
    assert.strictEqual(pending[0].slot_id, 12);
    assert.strictEqual(pending[0].entries.length, 2);

    const count = await getPendingCount();
    assert.strictEqual(count, 1);
  });

  it('syncs pending entries in FIFO order, sending exceptions only per TCH-002', async () => {
    // Enqueue 2 slots
    const entries1: PeriodAttendanceEntry[] = [
      { student_id: 1, status: 'HADIR' },
      { student_id: 2, status: 'SAKIT' },
    ];
    const entries2: PeriodAttendanceEntry[] = [
      { student_id: 1, status: 'HADIR' },
      { student_id: 2, status: 'ALPA' },
    ];

    await enqueueAttendance(10, '2026-09-16', entries1);
    await enqueueAttendance(11, '2026-09-16', entries2);

    let recordedPayload: any = null;
    let recordedHeaders: any = null;

    const originalPost = apiClient.post;
    apiClient.post = (async (path: string, payload: any, options: any) => {
      assert.strictEqual(path, '/period-attendance/sync/');
      recordedPayload = payload;
      recordedHeaders = options?.headers;
      return {
        data: { total: 2, succeeded: 2, failed: 0, results: [] },
        status: 200,
        headers: {},
      };
    }) as any;

    try {
      const syncResult = await syncPendingEntries();

      assert.strictEqual(syncResult.total, 2);
      assert.strictEqual(syncResult.succeeded, 2);
      assert.strictEqual(syncResult.failed, 0);

      // Verify exceptions only: student 1 (HADIR) is omitted, student 2 is included
      assert.strictEqual(recordedPayload.entries.length, 2);
      assert.strictEqual(recordedPayload.entries[0].slot_id, 10);
      assert.deepStrictEqual(recordedPayload.entries[0].exceptions, [{ student_id: 2, status: 'SAKIT' }]);
      assert.strictEqual(recordedPayload.entries[1].slot_id, 11);
      assert.deepStrictEqual(recordedPayload.entries[1].exceptions, [{ student_id: 2, status: 'ALPA' }]);

      // Verify Idempotency-Key header is present
      assert.ok(recordedHeaders['Idempotency-Key']);

      // Queue should now be empty of pending items
      const remaining = await getPendingCount();
      assert.strictEqual(remaining, 0);
    } finally {
      apiClient.post = originalPost;
    }
  });

  it('marks items as FAILED on network or server error without discarding them', async () => {
    const entries: PeriodAttendanceEntry[] = [
      { student_id: 1, status: 'ALPA' },
    ];
    await enqueueAttendance(5, '2026-09-16', entries);

    const originalPost = apiClient.post;
    apiClient.post = (async () => {
      const err: any = new Error('Server maintenance');
      err.response = { data: { error: 'Server maintenance' } };
      throw err;
    }) as any;

    try {
      const syncResult = await syncPendingEntries();
      assert.strictEqual(syncResult.total, 1);
      assert.strictEqual(syncResult.succeeded, 0);
      assert.strictEqual(syncResult.failed, 1);

      const pending = await getPendingQueue();
      assert.strictEqual(pending.length, 1);
      assert.strictEqual(pending[0].status, 'FAILED');
      assert.strictEqual(pending[0].attempts, 1);
      assert.strictEqual(pending[0].last_error, 'Server maintenance');
    } finally {
      apiClient.post = originalPost;
    }
  });
});
