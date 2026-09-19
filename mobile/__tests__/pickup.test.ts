/**
 * Pickup authorisations client layer (spec/05 ATT-015): endpoints, request shape, the validity-window presets
 * (WIB-aware), error codes and the no-storage guarantee. Server rules are tested in apps/attendance.
 */
import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import {
  createPickupAuthorization,
  fetchPickupAuthorizations,
  isLive,
  pickupErrorCode,
  revokePickupAuthorization,
  windowFor,
} from '../src/services/pickup.ts';
import { apiClient } from '../src/services/api.ts';
import type { PickupAuthorizationItem } from '../src/types/index.ts';

const item = (over: Partial<PickupAuthorizationItem> = {}): PickupAuthorizationItem => ({
  id: 1, student_id: 7, person_name: 'Pak Budi', relation: 'Paman', phone: '0812', photo_key: '',
  valid_from: '2026-09-19T03:00:00Z', valid_to: '2026-09-19T16:59:59Z', one_time: true, status: 'ACTIVE',
  used_at: null, revoked_at: null, qr_token: 'tok', ...over,
});

async function withClient<T>(
  method: 'get' | 'post', payload: unknown, run: (calls: Array<{ path: string; body?: unknown }>) => Promise<T>,
): Promise<T> {
  const original = apiClient[method];
  const calls: Array<{ path: string; body?: unknown }> = [];
  (apiClient as any)[method] = async (path: string, body?: unknown): Promise<any> => {
    calls.push({ path, body });
    return { data: payload, status: 200, headers: {} };
  };
  try {
    return await run(calls);
  } finally {
    (apiClient as any)[method] = original;
  }
}

describe('pickup validity windows', () => {
  it('TODAY runs to the last second of the WIB day', () => {
    // 10:00 UTC is 17:00 WIB on 19 Sep; the WIB day ends 23:59:59 WIB = 16:59:59 UTC.
    const w = windowFor('TODAY', new Date('2026-09-19T10:00:00Z'));
    assert.strictEqual(w.valid_from, '2026-09-19T10:00:00.000Z');
    assert.strictEqual(w.valid_to, '2026-09-19T16:59:59.000Z');
  });

  it('after 17:00 UTC it is already tomorrow in WIB, so TODAY ends on the next UTC day', () => {
    // 18:00 UTC on 19 Sep is 01:00 WIB on 20 Sep.
    assert.strictEqual(windowFor('TODAY', new Date('2026-09-19T18:00:00Z')).valid_to, '2026-09-20T16:59:59.000Z');
  });

  it('WEEK and MONTH are 7 and 30 days from now', () => {
    const now = new Date('2026-09-19T10:00:00Z');
    assert.strictEqual(windowFor('WEEK', now).valid_to, '2026-09-26T10:00:00.000Z');
    assert.strictEqual(windowFor('MONTH', now).valid_to, '2026-10-19T10:00:00.000Z');
  });

  it('always ends after it starts', () => {
    const now = new Date('2026-09-19T16:59:59Z'); // the last second of the WIB day
    for (const preset of ['TODAY', 'WEEK', 'MONTH'] as const) {
      const w = windowFor(preset, now);
      assert.ok(new Date(w.valid_to).getTime() >= new Date(w.valid_from).getTime(), preset);
    }
  });
});

describe('pickup authorisation requests', () => {
  it('lists a child’s authorisations by student id', async () => {
    await withClient('get', [item(), item({ id: 2, status: 'USED', qr_token: null })], async (calls) => {
      const result = await fetchPickupAuthorizations(7);
      assert.deepStrictEqual(result.map((r) => r.id), [1, 2]);
      assert.deepStrictEqual(calls.map((c) => c.path), ['/pickup-authorizations/?student_id=7']);
    });
  });

  it('treats an unexpected response body as an empty list', async () => {
    await withClient('get', { detail: 'nope' }, async () => assert.deepStrictEqual(await fetchPickupAuthorizations(7), []));
  });

  it('creates with trimmed fields, the preset window and the one-time flag', async () => {
    const now = new Date('2026-09-19T10:00:00Z');
    await withClient('post', item(), async (calls) => {
      const created = await createPickupAuthorization(
        { studentId: 7, personName: '  Pak Budi ', relation: ' Paman', phone: ' 0812 ', preset: 'WEEK', oneTime: false }, now,
      );
      assert.strictEqual(created.id, 1);
      assert.strictEqual(calls[0].path, '/pickup-authorizations/');
      assert.deepStrictEqual(calls[0].body, {
        student_id: 7, person_name: 'Pak Budi', relation: 'Paman', phone: '0812',
        valid_from: '2026-09-19T10:00:00.000Z', valid_to: '2026-09-26T10:00:00.000Z', one_time: false,
      });
    });
  });

  it('never sends a photo key (photo upload is a separate item)', async () => {
    await withClient('post', item(), async (calls) => {
      await createPickupAuthorization({ studentId: 7, personName: 'A', relation: '', phone: '', preset: 'TODAY', oneTime: true });
      assert.ok(!('photo_key' in (calls[0].body as object)));
    });
  });

  it('revokes by id', async () => {
    await withClient('post', item({ status: 'REVOKED', qr_token: null }), async (calls) => {
      const revoked = await revokePickupAuthorization(5);
      assert.strictEqual(revoked.status, 'REVOKED');
      assert.strictEqual(calls[0].path, '/pickup-authorizations/5/revoke/');
    });
  });
});

describe('pickup helpers', () => {
  it('reads the machine-readable error code and ignores anything else', () => {
    assert.strictEqual(pickupErrorCode({ response: { data: { code: 'PICKUP_NOT_GUARDIAN' } } }), 'PICKUP_NOT_GUARDIAN');
    assert.strictEqual(pickupErrorCode({ response: { data: { error: 'x' } } }), null);
    assert.strictEqual(pickupErrorCode(new Error('offline')), null);
    assert.strictEqual(pickupErrorCode(null), null);
  });

  it('only ACTIVE and SCHEDULED authorisations are live', () => {
    for (const status of ['ACTIVE', 'SCHEDULED'] as const) assert.strictEqual(isLive(item({ status })), true, status);
    for (const status of ['USED', 'EXPIRED', 'REVOKED'] as const) assert.strictEqual(isLive(item({ status })), false, status);
  });

  it('never writes the list (phone numbers, live QR tokens) to on-device storage', () => {
    const source = readFileSync(new URL('../src/services/pickup.ts', import.meta.url), 'utf8');
    assert.doesNotMatch(source, /from\s+['"][^'"]*storage/);
    assert.doesNotMatch(source, /\bcacheSet\b|\bAsyncStorage\b|\bsetItem\b/);
  });
});
