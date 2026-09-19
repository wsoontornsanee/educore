/**
 * Cold-start and tap-while-open push notification handling (spec/08 PAR-004).
 *
 * Mocking note: the task brief for this test specified a
 * `Module.prototype.require` monkey-patch to intercept `require('expo-notifications')`
 * from inside pushNotifications.ts. That technique does not work reliably in
 * this repo: mobile/package.json declares `"type": "module"`, so
 * pushNotifications.ts (which has top-level `export`s) loads as an ES module.
 * When this test's `require('../src/services/pushNotifications.ts')` pulls it
 * in via Node's require(esm) interop, the target module's own internal
 * `require('expo-notifications')` call does not route through
 * `Module.prototype.require`, nor (confirmed experimentally) through the
 * lower-level `Module._load` static hook either, once the outer require is
 * itself reached from an ESM `createRequire` inside `node --test` — so
 * `capturedListener` never got set and the assertion failed no matter which
 * require-interception layer was patched.
 *
 * Instead, this test uses a small test-only seam added to
 * pushNotifications.ts: `__setNotificationsModuleForTesting(mock)`, which
 * directly swaps the module-level `Notifications` reference. This keeps the
 * same test assertions/intent as the brief (verifying that
 * `subscribeToNotificationResponseReceived` extracts `notification.request
 * .content.data` from a tap response and forwards it to the caller's
 * callback, and that `getInitialNotificationResponse` resolves null when
 * there is no response) without depending on fragile Node module-loading
 * internals.
 */
import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import {
  __setNotificationsModuleForTesting,
  subscribeToNotificationResponseReceived,
  getInitialNotificationResponse,
} from '../src/services/pushNotifications.ts';

describe('Push Notification Data Extraction', () => {
  it('subscribeToNotificationResponseReceived extracts data from a tap response', () => {
    const calls: any[] = [];
    let capturedListener = null as ((response: any) => void) | null;

    __setNotificationsModuleForTesting({
      addNotificationResponseReceivedListener: (cb: (response: any) => void) => {
        capturedListener = cb;
        return { remove: () => {} };
      },
      getLastNotificationResponseAsync: async () => null,
      getPermissionsAsync: async () => ({ status: 'granted' }),
      requestPermissionsAsync: async () => ({ status: 'granted' }),
      addNotificationReceivedListener: () => ({ remove: () => {} }),
    });

    const sub = subscribeToNotificationResponseReceived((data: any) => calls.push(data));
    assert.ok(capturedListener);
    capturedListener!({
      notification: { request: { content: { data: { type: 'ARRIVAL', student_id: 42, date: '2026-09-17' } } } },
    });

    assert.strictEqual(calls.length, 1);
    assert.deepStrictEqual(calls[0], { type: 'ARRIVAL', student_id: 42, date: '2026-09-17' });
    sub.remove();
  });

  it('subscribeToNotificationResponseReceived extracts SUBSTITUTE_ASSIGNED tap data', () => {
    const calls: any[] = [];
    let capturedListener = null as ((response: any) => void) | null;

    __setNotificationsModuleForTesting({
      addNotificationResponseReceivedListener: (cb: (response: any) => void) => {
        capturedListener = cb;
        return { remove: () => {} };
      },
      getLastNotificationResponseAsync: async () => null,
      getPermissionsAsync: async () => ({ status: 'granted' }),
      requestPermissionsAsync: async () => ({ status: 'granted' }),
      addNotificationReceivedListener: () => ({ remove: () => {} }),
    });

    const sub = subscribeToNotificationResponseReceived((data: any) => calls.push(data));
    assert.ok(capturedListener);
    capturedListener!({
      notification: {
        request: {
          content: {
            data: {
              type: 'SUBSTITUTE_ASSIGNED',
              substitution_id: 88,
              slot_id: 2,
              class_group: '7B',
              subject: 'IPA Terpadu',
              date: '2026-09-17',
              period_no: '1',
              original_teacher: 'Pak Budi',
            },
          },
        },
      },
    });

    assert.strictEqual(calls.length, 1);
    assert.strictEqual(calls[0].type, 'SUBSTITUTE_ASSIGNED');
    assert.strictEqual(calls[0].substitution_id, 88);
    assert.strictEqual(calls[0].slot_id, 2);
    sub.remove();
  });

  it('getInitialNotificationResponse returns null when app was not launched by a notification', async () => {
    __setNotificationsModuleForTesting({
      addNotificationResponseReceivedListener: () => ({ remove: () => {} }),
      getLastNotificationResponseAsync: async () => null,
      getPermissionsAsync: async () => ({ status: 'granted' }),
      requestPermissionsAsync: async () => ({ status: 'granted' }),
      addNotificationReceivedListener: () => ({ remove: () => {} }),
    });

    const result = await getInitialNotificationResponse();
    assert.strictEqual(result, null);
  });
});
