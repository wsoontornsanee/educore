/**
 * Mobile Analytics Event Instrumentation Unit Tests (spec/08 §5, spec/15 RPT-015).
 */
import { describe, it, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import {
  clearAllAnalyticsForTesting,
  enqueueEvent,
  getPendingEventCount,
  syncPendingEvents,
} from '../src/services/analyticsQueue.ts';
import { track } from '../src/services/analytics.ts';
import { apiClient } from '../src/services/api.ts';

describe('Analytics Event Queue', () => {
  beforeEach(async () => {
    await clearAllAnalyticsForTesting();
  });

  it('enqueues an event as PENDING', async () => {
    await enqueueEvent('app_open', null);
    const count = await getPendingEventCount();
    assert.strictEqual(count, 1);
  });

  it('syncs pending events in one batch to /analytics/events/', async () => {
    await enqueueEvent('app_open', null);
    await enqueueEvent('invoice_view', 5);

    let sentPayload: any = null;
    const originalPost = apiClient.post;
    (apiClient as any).post = async (url: string, data: any) => {
      if (url === '/analytics/events/') {
        sentPayload = data;
        return { data: { accepted: 2 }, status: 201, headers: {} };
      }
      return originalPost(url, data);
    };

    try {
      const result = await syncPendingEvents();
      assert.strictEqual(result.total, 2);
      assert.strictEqual(result.succeeded, 2);
      assert.strictEqual(result.failed, 0);
      assert.strictEqual(sentPayload.events.length, 2);
      assert.strictEqual(sentPayload.events[0].event_name, 'app_open');
      assert.strictEqual(sentPayload.events[1].school_id, 5);

      const remaining = await getPendingEventCount();
      assert.strictEqual(remaining, 0);
    } finally {
      (apiClient as any).post = originalPost;
    }
  });

  it('marks events FAILED (retryable) on network error, does not throw', async () => {
    await enqueueEvent('app_open', null);

    const originalPost = apiClient.post;
    (apiClient as any).post = async () => {
      throw new Error('Network Error');
    };

    try {
      const result = await syncPendingEvents();
      assert.strictEqual(result.failed, 1);
      assert.strictEqual(result.succeeded, 0);
      // Still pending (FAILED counts as retryable pending), not lost:
      const remaining = await getPendingEventCount();
      assert.strictEqual(remaining, 1);
    } finally {
      (apiClient as any).post = originalPost;
    }
  });

  it('track() never throws even when the queue/network fails', async () => {
    // track() fires enqueue+sync fire-and-forget; mock the network and flush
    // the underlying promise chain so it can't leak an in-flight sync (and a
    // held syncInFlight guard) into a later test.
    const originalPost = apiClient.post;
    (apiClient as any).post = async () => ({ data: { accepted: 0 }, status: 201, headers: {} });

    try {
      assert.doesNotThrow(() => track('app_open'));
      assert.doesNotThrow(() => track('invoice_view', 5));
      // Flush microtasks: enqueueEvent() -> .then(syncPendingEvents) -> .catch().
      await new Promise((resolve) => setTimeout(resolve, 0));
      await new Promise((resolve) => setTimeout(resolve, 0));
    } finally {
      (apiClient as any).post = originalPost;
    }
  });

  it('reuses the same Idempotency-Key when retrying the same pending batch (lost-response retry)', async () => {
    await enqueueEvent('app_open', null);
    await enqueueEvent('invoice_view', 5);

    const sentKeys: string[] = [];
    const originalPost = apiClient.post;
    // Simulate a lost response: the server call "succeeds" server-side but we
    // never see the response, so the client marks the batch FAILED and retries.
    (apiClient as any).post = async (url: string, _data: any, options: any) => {
      if (url === '/analytics/events/') {
        sentKeys.push(options?.headers?.['Idempotency-Key']);
        throw new Error('Network Error');
      }
      return originalPost(url, _data, options);
    };

    try {
      const first = await syncPendingEvents();
      assert.strictEqual(first.failed, 2);
      const second = await syncPendingEvents();
      assert.strictEqual(second.failed, 2);

      assert.strictEqual(sentKeys.length, 2);
      assert.ok(sentKeys[0]);
      assert.strictEqual(sentKeys[0], sentKeys[1]);
    } finally {
      (apiClient as any).post = originalPost;
    }
  });
});
