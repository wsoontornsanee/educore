/**
 * Parent Broadcast Service Unit Tests (spec/08 §2 Messages tab, PAR-015).
 */
import { describe, it, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { fetchStudentBroadcasts } from '../src/services/broadcasts.ts';
import { api } from '../src/services/api.ts';
import { clearCachedData } from '../src/services/storage.ts';
import type { BroadcastItem } from '../src/types/index.ts';

const makeBroadcast = (overrides: Partial<BroadcastItem> = {}): BroadcastItem => ({
  id: 1,
  title: 'Pengumuman Ujian Tengah Semester',
  body: 'Ujian tengah semester akan dimulai pada 1 Desember 2026. Pastikan ananda mempersiapkan diri dengan baik.',
  sent_at: '2026-09-20T08:00:00+07:00',
  sender_name: 'Bu Siti Rahayu',
  class_group_name: 'X IPA 1',
  class_group_id: 11,
  ...overrides,
});

describe('Parent Broadcast Service', () => {
  beforeEach(async () => {
    await clearCachedData();
  });

  describe('fetchStudentBroadcasts', () => {
    it('fetches broadcasts from API and caches the response', async () => {
      const broadcasts = [makeBroadcast()];
      api.get = (async () => ({ data: { results: broadcasts }, status: 200, headers: {} })) as any;

      const result = await fetchStudentBroadcasts(101);
      assert.equal(result.isOfflineCached, false);
      assert.equal(result.broadcasts.length, 1);
      assert.equal(result.broadcasts[0].title, 'Pengumuman Ujian Tengah Semester');
      assert.equal(result.broadcasts[0].sender_name, 'Bu Siti Rahayu');
      assert.ok(result.lastUpdated.length > 0);
    });

    it('falls back to offline cache when the API call fails (PAR-015)', async () => {
      const broadcasts = [makeBroadcast({ id: 5, title: 'Libur Nasional' })];
      api.get = (async () => ({ data: { results: broadcasts }, status: 200, headers: {} })) as any;
      await fetchStudentBroadcasts(101); // populate cache

      api.get = (async () => {
        throw new Error('network down');
      }) as any;
      const result = await fetchStudentBroadcasts(101);
      assert.equal(result.isOfflineCached, true);
      assert.equal(result.broadcasts[0].id, 5);
      assert.equal(result.broadcasts[0].title, 'Libur Nasional');
    });

    it('throws when there is no cache and the API fails', async () => {
      api.get = (async () => {
        throw new Error('network down');
      }) as any;
      await assert.rejects(() => fetchStudentBroadcasts(999));
    });

    it('returns empty list when no broadcasts exist', async () => {
      api.get = (async () => ({ data: { results: [] }, status: 200, headers: {} })) as any;
      const result = await fetchStudentBroadcasts(101);
      assert.equal(result.broadcasts.length, 0);
      assert.equal(result.isOfflineCached, false);
    });
  });
});
