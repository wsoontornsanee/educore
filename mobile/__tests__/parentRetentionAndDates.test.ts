import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { todayWib } from '../src/services/localDate.ts';
import {
  cacheGet,
  cacheSet,
  clearAuth,
  getItem,
  getLastChildId,
  saveLastChildId,
  saveTokens,
  StorageKeys,
} from '../src/services/storage.ts';

describe('WIB local date (id-ID first)', () => {
  it('returns the Indonesian calendar date, not the UTC one, before 07:00 WIB', () => {
    // 2026-09-16 22:30 UTC == 2026-09-17 05:30 WIB
    const now = new Date('2026-09-16T22:30:00.000Z');
    assert.strictEqual(now.toISOString().split('T')[0], '2026-09-16');
    assert.strictEqual(todayWib(now), '2026-09-17');
  });

  it('agrees with UTC during Indonesian working hours', () => {
    // 2026-09-16 03:00 UTC == 2026-09-16 10:00 WIB
    assert.strictEqual(todayWib(new Date('2026-09-16T03:00:00.000Z')), '2026-09-16');
  });
});

describe('Logout retention purge (PAR-020)', () => {
  it('removes tokens, last-selected child and every per-child cache entry', async () => {
    await saveTokens('access-token', 'refresh-token');
    await saveLastChildId(7);
    await cacheSet('educore_parent_home_7', { today: null, outstanding: [] });
    await cacheSet('educore_parent_attendance_7', []);
    await cacheSet('educore_parent_invoices_7', []);

    assert.notStrictEqual(await cacheGet('educore_parent_home_7'), null);

    await clearAuth();

    assert.strictEqual(await getItem(StorageKeys.ACCESS_TOKEN), null);
    assert.strictEqual(await getItem(StorageKeys.REFRESH_TOKEN), null);
    assert.strictEqual(await getLastChildId(), null);
    assert.strictEqual(await cacheGet('educore_parent_home_7'), null);
    assert.strictEqual(await cacheGet('educore_parent_attendance_7'), null);
    assert.strictEqual(await cacheGet('educore_parent_invoices_7'), null);
    assert.strictEqual(await getItem(StorageKeys.CACHE_KEY_REGISTRY), null);
  });
});
