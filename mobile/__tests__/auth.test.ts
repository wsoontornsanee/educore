/**
 * Auth Service and Storage Unit Tests.
 */
import { describe, it, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { checkAuth, login, logout } from '../src/services/auth.ts';
import { apiClient } from '../src/services/api.ts';
import { clearAuth, getItem, StorageKeys } from '../src/services/storage.ts';

describe('Mobile Auth Service', () => {
  beforeEach(async () => {
    await clearAuth();
  });

  it('successfully logs in with phone identifier and persists tokens', async () => {
    const mockAuthResponse = {
      data: {
        access: 'mock-access-token-123',
        refresh: 'mock-refresh-token-456',
        user: {
          id: 1,
          full_name: 'Ustadz Ahmad',
          phone_e164: '+6281234567890',
          email: 'ahmad@sekolah.sch.id',
          foundation_id: 10,
          roles: [{ id: 1, role: 'teacher', scope_type: 'SCHOOL', scope_id: 1 }],
        },
      },
      status: 200,
      headers: {},
    };

    const originalPost = apiClient.post;
    apiClient.post = (async (path: string, body: any) => {
      assert.strictEqual(path, '/auth/token/');
      assert.strictEqual(body.identifier, '+6281234567890');
      assert.strictEqual(body.password, 'SecretPass123!');
      return mockAuthResponse;
    }) as any;

    try {
      const result = await login('+6281234567890', 'SecretPass123!');

      assert.strictEqual(result.access, 'mock-access-token-123');
      assert.strictEqual(result.user.full_name, 'Ustadz Ahmad');

      // Verify persisted in storage
      const storedAccess = await getItem(StorageKeys.ACCESS_TOKEN);
      const storedRefresh = await getItem(StorageKeys.REFRESH_TOKEN);
      assert.strictEqual(storedAccess, 'mock-access-token-123');
      assert.strictEqual(storedRefresh, 'mock-refresh-token-456');

      // Verify checkAuth status
      const authStatus = await checkAuth();
      assert.strictEqual(authStatus.authenticated, true);
      assert.strictEqual(authStatus.user?.full_name, 'Ustadz Ahmad');
    } finally {
      apiClient.post = originalPost;
    }
  });

  it('logout properly clears stored tokens and user profile', async () => {
    const mockAuthResponse = {
      data: {
        access: 'token-abc',
        refresh: 'token-def',
        user: { id: 2, full_name: 'Ibu Fatimah', phone_e164: '+62811111111', email: null, foundation_id: 1, roles: [] },
      },
      status: 200,
      headers: {},
    };

    const originalPost = apiClient.post;
    apiClient.post = (async () => mockAuthResponse) as any;

    try {
      await login('+62811111111', 'pass');
      await logout();

      const authStatus = await checkAuth();
      assert.strictEqual(authStatus.authenticated, false);
      assert.strictEqual(authStatus.user, null);
    } finally {
      apiClient.post = originalPost;
    }
  });
});
