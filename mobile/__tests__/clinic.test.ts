/**
 * Clinic Visit History & Health Profile Service Unit Tests.
 *
 * Parent App: Guardian Read Scoping for Clinic Visit History (Notion Open Item).
 * The backend guardian-scoping (get_guardian_student_ids/can_guardian_access_student)
 * was already verified end-to-end in apps/campus/tests/test_clinic_views.py
 * (PR #159) — this covers the mobile client's data layer only.
 */
import { describe, it, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import {
  getClinicVisitsCacheKey,
  getHealthProfileCacheKey,
  fetchClinicVisits,
  fetchHealthProfile,
} from '../src/services/clinic.ts';
import { apiClient } from '../src/services/api.ts';
import { cacheGet, cacheSet, removeItem } from '../src/services/storage.ts';
import type { ClinicVisitItem, HealthProfileItem } from '../src/types/index.ts';

describe('Clinic Visit History & Health Profile Service', () => {
  const studentId = 101;
  const visitsCacheKey = getClinicVisitsCacheKey(studentId);
  const profileCacheKey = getHealthProfileCacheKey(studentId);

  const sampleVisits: ClinicVisitItem[] = [
    {
      id: 1,
      school: 1,
      student: studentId,
      student_name: 'Ahmad Santoso',
      occurred_at: '2026-09-15T02:00:00Z',
      complaint: 'Demam tinggi',
      treatment: 'Diberikan parasetamol dan istirahat di UKS',
      vitals: { temperature_c: 38.5 },
      medication_given: 3,
      medication_name: 'Paracetamol',
      medication_quantity_used: 1,
      outcome: 'SENT_HOME',
      handled_by: 5,
      handled_by_name: 'Bu Wati',
      guardian_consent_confirmed: true,
      guardian_consent_note: '',
      guardian_notified_at: '2026-09-15T02:05:00Z',
      created_at: '2026-09-15T02:00:00Z',
      updated_at: '2026-09-15T02:05:00Z',
    },
  ];

  const sampleProfile: HealthProfileItem = {
    id: 1,
    student: studentId,
    blood_type: 'O',
    allergies: ['Penisilin'],
    chronic_conditions: [],
    medications: [],
    emergency_contacts: [],
    has_medical_alert: true,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  };

  beforeEach(async () => {
    await removeItem(visitsCacheKey);
    await removeItem(profileCacheKey);
  });

  describe('fetchClinicVisits & Offline Caching (PAR-015)', () => {
    it('fetches online clinic visits and caches them locally', async () => {
      const originalGet = apiClient.get;
      apiClient.get = async <T>(_path: string): Promise<any> => {
        return { data: { results: sampleVisits }, status: 200, headers: {} };
      };

      try {
        const result = await fetchClinicVisits(studentId);
        assert.deepStrictEqual(result, sampleVisits);

        const cached = await cacheGet<ClinicVisitItem[]>(visitsCacheKey);
        assert.ok(cached !== null);
        assert.deepStrictEqual(cached.value, sampleVisits);
      } finally {
        apiClient.get = originalGet;
      }
    });

    it('accepts a bare array response (non-paginated)', async () => {
      const originalGet = apiClient.get;
      apiClient.get = async <T>(_path: string): Promise<any> => {
        return { data: sampleVisits, status: 200, headers: {} };
      };

      try {
        const result = await fetchClinicVisits(studentId);
        assert.deepStrictEqual(result, sampleVisits);
      } finally {
        apiClient.get = originalGet;
      }
    });

    it('scopes the request to the given student via query param', async () => {
      const originalGet = apiClient.get;
      let capturedPath = '';
      apiClient.get = async <T>(path: string): Promise<any> => {
        capturedPath = path;
        return { data: { results: [] }, status: 200, headers: {} };
      };

      try {
        await fetchClinicVisits(studentId);
        assert.ok(capturedPath.includes(`student_id=${studentId}`));
      } finally {
        apiClient.get = originalGet;
      }
    });

    it('falls back to local cached visits when the network request fails', async () => {
      await cacheSet(visitsCacheKey, sampleVisits);

      const originalGet = apiClient.get;
      apiClient.get = async <T>(_path: string): Promise<any> => {
        throw new Error('Network error');
      };

      try {
        const result = await fetchClinicVisits(studentId);
        assert.deepStrictEqual(result, sampleVisits);
      } finally {
        apiClient.get = originalGet;
      }
    });

    it('throws when offline and no cache is present', async () => {
      const originalGet = apiClient.get;
      apiClient.get = async <T>(_path: string): Promise<any> => {
        throw new Error('Network error');
      };

      try {
        await assert.rejects(() => fetchClinicVisits(studentId));
      } finally {
        apiClient.get = originalGet;
      }
    });
  });

  describe('fetchHealthProfile & Offline Caching (PAR-015)', () => {
    it('fetches the online health profile and caches it locally', async () => {
      const originalGet = apiClient.get;
      apiClient.get = async <T>(_path: string): Promise<any> => {
        return { data: sampleProfile, status: 200, headers: {} };
      };

      try {
        const result = await fetchHealthProfile(studentId);
        assert.deepStrictEqual(result, sampleProfile);

        const cached = await cacheGet<HealthProfileItem>(profileCacheKey);
        assert.ok(cached !== null);
        assert.deepStrictEqual(cached.value, sampleProfile);
      } finally {
        apiClient.get = originalGet;
      }
    });

    it('falls back to the local cached profile when the network request fails', async () => {
      await cacheSet(profileCacheKey, sampleProfile);

      const originalGet = apiClient.get;
      apiClient.get = async <T>(_path: string): Promise<any> => {
        throw new Error('Network error');
      };

      try {
        const result = await fetchHealthProfile(studentId);
        assert.deepStrictEqual(result, sampleProfile);
      } finally {
        apiClient.get = originalGet;
      }
    });

    it('throws when offline and no cache is present', async () => {
      const originalGet = apiClient.get;
      apiClient.get = async <T>(_path: string): Promise<any> => {
        throw new Error('Network error');
      };

      try {
        await assert.rejects(() => fetchHealthProfile(studentId));
      } finally {
        apiClient.get = originalGet;
      }
    });
  });
});
