/**
 * Parent Permission Slip Service Unit Tests (spec/08 §2 Messages tab, PAR-012, PAR-015).
 */
import { describe, it, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import {
  acknowledgePermissionSlip,
  fetchStudentPermissionSlips,
  resolveSlipStatus,
} from '../src/services/permissionSlips.ts';
import { api } from '../src/services/api.ts';
import { clearCachedData } from '../src/services/storage.ts';
import type { PermissionSlipItem } from '../src/types/index.ts';

const makeSlip = (overrides: Partial<PermissionSlipItem> = {}): PermissionSlipItem => ({
  id: 1,
  title: 'Kunjungan Museum Nasional',
  description: 'Study tour sejarah kelas X IPA 1',
  event_date: '2026-10-01',
  location: 'Museum Nasional, Jakarta',
  due_at: '2026-09-25T23:59:59+07:00',
  is_closed: false,
  class_group_id: 11,
  class_group_name: 'X IPA 1',
  school_id: 2,
  created_by_name: 'Bu Siti Rahayu',
  created_at: '2026-09-17T02:00:00+07:00',
  tally: { total_enrolled: 30, approved: 21, declined: 4, pending: 5 },
  my_response: null,
  my_responded_at: null,
  my_pending: true,
  ...overrides,
});

describe('Parent Permission Slip Service', () => {
  beforeEach(async () => {
    await clearCachedData();
  });

  describe('fetchStudentPermissionSlips', () => {
    it('fetches slips from API and caches the response', async () => {
      const slips = [makeSlip()];
      api.get = (async () => ({ data: { results: slips }, status: 200, headers: {} })) as any;

      const result = await fetchStudentPermissionSlips(101);
      assert.equal(result.isOfflineCached, false);
      assert.equal(result.slips.length, 1);
      assert.equal(result.slips[0].title, 'Kunjungan Museum Nasional');
      assert.ok(result.lastUpdated.length > 0);
    });

    it('falls back to offline cache when the API call fails (PAR-015)', async () => {
      const slips = [makeSlip({ id: 7, title: 'Izin Kemah Pramuka' })];
      api.get = (async () => ({ data: { results: slips }, status: 200, headers: {} })) as any;
      await fetchStudentPermissionSlips(101); // populate cache

      api.get = (async () => {
        throw new Error('network down');
      }) as any;
      const result = await fetchStudentPermissionSlips(101);
      assert.equal(result.isOfflineCached, true);
      assert.equal(result.slips[0].id, 7);
    });

    it('throws when there is no cache and the API fails', async () => {
      api.get = (async () => {
        throw new Error('network down');
      }) as any;
      await assert.rejects(() => fetchStudentPermissionSlips(999));
    });
  });

  describe('acknowledgePermissionSlip', () => {
    it('posts the signed acknowledgement and returns the server timestamp', async () => {
      let capturedPath = '';
      let capturedBody: any = null;
      api.post = (async (path: string, body: any) => {
        capturedPath = path;
        capturedBody = body;
        return {
          data: {
            id: 55,
            permission_slip_id: 1,
            student_id: 101,
            response: 'APPROVED',
            responded_at: '2026-09-17T03:30:00+07:00',
            signature: 'Bapak Budi Wijaya',
          },
          status: 201,
          headers: {},
        } as any;
      }) as any;

      const result = await acknowledgePermissionSlip(1, {
        student_id: 101,
        response: 'APPROVED',
        signature: 'Bapak Budi Wijaya',
      });
      assert.equal(capturedPath, '/academic/permission-slips/1/acknowledge/');
      assert.equal(capturedBody.response, 'APPROVED');
      assert.equal(capturedBody.signature, 'Bapak Budi Wijaya');
      assert.equal(result.responded_at, '2026-09-17T03:30:00+07:00');
    });

    it('propagates server errors (slip closed, not linked)', async () => {
      api.post = (async () => {
        throw new Error('PERMISSION_SLIP_CLOSED: the acknowledgement window has closed.');
      }) as any;
      await assert.rejects(() =>
        acknowledgePermissionSlip(2, { student_id: 101, response: 'DECLINED', signature: 'Bapak Budi' }),
      );
    });
  });

  describe('resolveSlipStatus', () => {
    it('prefers the guardian\u2019s own effective response over pending', () => {
      assert.equal(resolveSlipStatus(makeSlip({ my_response: 'APPROVED' })), 'APPROVED');
      assert.equal(resolveSlipStatus(makeSlip({ my_response: 'DECLINED' })), 'DECLINED');
      assert.equal(resolveSlipStatus(makeSlip({ my_response: null })), 'PENDING');
    });

    it('reports PENDING for a closed slip the guardian never signed', () => {
      const slip = makeSlip({ my_response: null, is_closed: true, my_pending: false });
      assert.equal(resolveSlipStatus(slip), 'PENDING');
    });
  });
});
