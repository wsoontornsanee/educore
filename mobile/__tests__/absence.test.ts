/**
 * Absence Request Service Unit Tests (PAR-011, ATT-002, PAR-015).
 */
import { describe, it, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import {
  MAX_ABSENCE_ATTACHMENT_BYTES,
  fetchAbsenceRequests,
  getAbsenceCacheKey,
  submitAbsenceRequest,
  validateAbsenceAttachment,
} from '../src/services/absence.ts';
import { apiClient } from '../src/services/api.ts';
import { cacheGet, cacheSet, removeItem } from '../src/services/storage.ts';
import type { AbsenceRequestItem, CreateAbsenceRequestPayload } from '../src/types/index.ts';

describe('Absence Request Service (PAR-011, ATT-002, PAR-015)', () => {
  const studentId = 101;
  const cacheKey = getAbsenceCacheKey(studentId);

  const sampleRequests: AbsenceRequestItem[] = [
    {
      id: 1,
      student_id: studentId,
      student_name: 'Ahmad Santoso',
      requested_by_id: 201,
      requested_by_name: 'Bapak Joko Santoso',
      type: 'SAKIT',
      date_from: '2026-09-20',
      date_to: '2026-09-22',
      reason: 'Demam tinggi',
      attachment_url: 'https://example.com/surat.jpg',
      status: 'PENDING',
      created_at: '2026-09-20T07:00:00Z',
    },
    {
      id: 2,
      student_id: studentId,
      student_name: 'Ahmad Santoso',
      requested_by_id: 201,
      requested_by_name: 'Bapak Joko Santoso',
      type: 'IZIN',
      date_from: '2026-09-10',
      date_to: '2026-09-11',
      reason: 'Acara keluarga',
      attachment_url: null,
      status: 'APPROVED',
      decision_note: 'Disetujui wali kelas',
      decided_at: '2026-09-10T08:00:00Z',
      decided_by_name: 'Ibu Guru Siti',
      created_at: '2026-09-09T10:00:00Z',
    },
  ];

  beforeEach(async () => {
    await removeItem(cacheKey);
  });

  describe('validateAbsenceAttachment (PAR-011)', () => {
    it('returns valid when attachment size is undefined or null', () => {
      assert.strictEqual(validateAbsenceAttachment(null).valid, true);
      assert.strictEqual(validateAbsenceAttachment(undefined).valid, true);
    });

    it('returns valid when attachment size is within 1MB limit', () => {
      assert.strictEqual(validateAbsenceAttachment(500 * 1024).valid, true);
      assert.strictEqual(validateAbsenceAttachment(MAX_ABSENCE_ATTACHMENT_BYTES).valid, true);
    });

    it('rejects attachments larger than 1MB (1024*1024 bytes)', () => {
      const result = validateAbsenceAttachment(MAX_ABSENCE_ATTACHMENT_BYTES + 1);
      assert.strictEqual(result.valid, false);
      assert.ok(result.error?.includes('1MB'));
      assert.ok(result.error?.includes('PAR-011'));
    });
  });

  describe('fetchAbsenceRequests & Offline Caching (PAR-015)', () => {
    it('fetches online absence requests and caches them locally', async () => {
      const originalGet = apiClient.get;
      apiClient.get = async <T>(_path: string): Promise<any> => {
        return { data: sampleRequests, status: 200, headers: {} };
      };

      try {
        const result = await fetchAbsenceRequests(studentId);
        assert.deepStrictEqual(result, sampleRequests);

        // Verify cached
        const cached = await cacheGet<AbsenceRequestItem[]>(cacheKey);
        assert.ok(cached !== null);
        assert.deepStrictEqual(cached.value, sampleRequests);
      } finally {
        apiClient.get = originalGet;
      }
    });

    it('falls back to local cached requests when network request fails', async () => {
      await cacheSet(cacheKey, sampleRequests);

      const originalGet = apiClient.get;
      apiClient.get = async <T>(_path: string): Promise<any> => {
        throw new Error('Network error');
      };

      try {
        const result = await fetchAbsenceRequests(studentId);
        assert.deepStrictEqual(result, sampleRequests);
      } finally {
        apiClient.get = originalGet;
      }
    });

    it('throws error when offline and no cache is present', async () => {
      const originalGet = apiClient.get;
      apiClient.get = async <T>(_path: string): Promise<any> => {
        throw new Error('Network connection unavailable');
      };

      try {
        await assert.rejects(async () => {
          await fetchAbsenceRequests(studentId);
        }, /Network connection unavailable/);
      } finally {
        apiClient.get = originalGet;
      }
    });
  });

  describe('submitAbsenceRequest', () => {
    it('rejects client-side before sending if attachment exceeds 1MB', async () => {
      const payload: CreateAbsenceRequestPayload = {
        student_id: studentId,
        type: 'SAKIT',
        date_from: '2026-09-25',
        date_to: '2026-09-26',
        reason: 'Sakit gigi',
        attachmentUri: 'file:///data/large.jpg',
        attachmentSize: 2 * 1024 * 1024, // 2MB
      };

      await assert.rejects(async () => {
        await submitAbsenceRequest(payload);
      }, /1MB/);
    });

    it('submits JSON payload when no attachment is provided and updates cache', async () => {
      const newRequest: AbsenceRequestItem = {
        id: 3,
        student_id: studentId,
        student_name: 'Ahmad Santoso',
        requested_by_id: 201,
        requested_by_name: 'Bapak Joko Santoso',
        type: 'IZIN',
        date_from: '2026-09-28',
        date_to: '2026-09-28',
        reason: 'Acara keluarga',
        attachment_url: null,
        status: 'PENDING',
        created_at: '2026-09-28T06:00:00Z',
      };

      await cacheSet(cacheKey, sampleRequests);

      const originalPost = apiClient.post;
      let sentBody: any = null;
      apiClient.post = async <T>(_path: string, body?: any): Promise<any> => {
        sentBody = body;
        return { data: newRequest, status: 201, headers: {} };
      };

      try {
        const payload: CreateAbsenceRequestPayload = {
          student_id: studentId,
          type: 'IZIN',
          date_from: '2026-09-28',
          date_to: '2026-09-28',
          reason: 'Acara keluarga',
        };

        const created = await submitAbsenceRequest(payload);
        assert.strictEqual(created.id, 3);
        assert.deepStrictEqual(sentBody, {
          date_from: '2026-09-28',
          date_to: '2026-09-28',
          type: 'IZIN',
          reason: 'Acara keluarga',
        });

        // Verify cache updated with new request prepended
        const cached = await cacheGet<AbsenceRequestItem[]>(cacheKey);
        assert.ok(cached !== null);
        assert.strictEqual(cached.value.length, 3);
        assert.strictEqual(cached.value[0].id, 3);
      } finally {
        apiClient.post = originalPost;
      }
    });

    it('submits FormData payload when attachment is present', async () => {
      const newRequest: AbsenceRequestItem = {
        id: 4,
        student_id: studentId,
        student_name: 'Ahmad Santoso',
        requested_by_id: 201,
        requested_by_name: 'Bapak Joko Santoso',
        type: 'SAKIT',
        date_from: '2026-09-29',
        date_to: '2026-09-30',
        reason: 'Demam',
        attachment_url: 'https://example.com/surat2.jpg',
        status: 'PENDING',
        created_at: '2026-09-29T06:00:00Z',
      };

      const originalPost = apiClient.post;
      let called = false;
      apiClient.post = async <T>(_path: string, _body?: any): Promise<any> => {
        called = true;
        return { data: newRequest, status: 201, headers: {} };
      };

      try {
        const payload: CreateAbsenceRequestPayload = {
          student_id: studentId,
          type: 'SAKIT',
          date_from: '2026-09-29',
          date_to: '2026-09-30',
          reason: 'Demam',
          attachmentUri: 'file:///photo.jpg',
          attachmentName: 'surat.jpg',
          attachmentType: 'image/jpeg',
          attachmentSize: 500 * 1024,
        };

        const created = await submitAbsenceRequest(payload);
        assert.strictEqual(created.id, 4);
        assert.strictEqual(called, true);
      } finally {
        apiClient.post = originalPost;
      }
    });
  });
});
