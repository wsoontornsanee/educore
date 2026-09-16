import { describe, it, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { requestOtp, verifyOtp } from '../src/services/parentAuth.ts';
import { apiClient } from '../src/services/api.ts';
import { clearAuth, getItem, StorageKeys } from '../src/services/storage.ts';

describe('Parent OTP Auth Service', () => {
  beforeEach(async () => {
    await clearAuth();
  });

  it('requests an OTP challenge for a phone number', async () => {
    const originalPost = apiClient.post;
    apiClient.post = (async (path: string, body: any) => {
      assert.strictEqual(path, '/auth/otp/request/');
      assert.strictEqual(body.phone_e164, '+6281234567890');
      return { data: { challenge_id: 42 }, status: 201, headers: {} };
    }) as any;

    try {
      const result = await requestOtp('+6281234567890');
      assert.strictEqual(result.challenge_id, 42);
    } finally {
      apiClient.post = originalPost;
    }
  });

  it('verifies an OTP and persists tokens + profile', async () => {
    const mockResponse = {
      data: {
        access: 'parent-access-token',
        refresh: 'parent-refresh-token',
        user: {
          id: 9,
          full_name: 'Ibu Siti',
          phone_e164: '+6281200000001',
          email: null,
          foundation_id: 3,
          roles: [{ id: 5, role: 'parent', scope_type: 'FOUNDATION', scope_id: 3 }],
        },
      },
      status: 200,
      headers: {},
    };

    const originalPost = apiClient.post;
    apiClient.post = (async (path: string, body: any) => {
      assert.strictEqual(path, '/auth/otp/verify/');
      assert.strictEqual(body.challenge_id, 42);
      assert.strictEqual(body.code, '111111');
      return mockResponse;
    }) as any;

    try {
      const result = await verifyOtp(42, '111111');
      assert.strictEqual(result.user.full_name, 'Ibu Siti');

      const storedAccess = await getItem(StorageKeys.ACCESS_TOKEN);
      assert.strictEqual(storedAccess, 'parent-access-token');
    } finally {
      apiClient.post = originalPost;
    }
  });
});
