/**
 * Mobile SSO Service Unit Tests (spec/14 §6, TASK-036).
 */
import { describe, it, beforeEach, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import {
  fetchSSOLinks,
  linkSSO,
  loginWithSSO,
  setMockProviderHandler,
  signInWithProvider,
  unlinkSSO,
} from '../src/services/sso.ts';
import { apiClient } from '../src/services/api.ts';
import { clearAuth, getItem, StorageKeys } from '../src/services/storage.ts';

describe('Mobile SSO Service', () => {
  beforeEach(async () => {
    await clearAuth();
    setMockProviderHandler(null);
  });

  afterEach(() => {
    setMockProviderHandler(null);
  });

  it('loginWithSSO sends provider and id_token and persists tokens in storage', async () => {
    const mockAuthResponse = {
      data: {
        access: 'sso-access-token-999',
        refresh: 'sso-refresh-token-888',
        user: {
          id: 10,
          full_name: 'Guru Google',
          phone_e164: '+6281299998888',
          email: 'guru@sekolah.sch.id',
          foundation_id: 5,
          roles: [{ id: 1, role: 'teacher', scope_type: 'SCHOOL', scope_id: 2 }],
        },
      },
      status: 200,
      headers: {},
    };

    const originalPost = apiClient.post;
    let postedPath = '';
    let postedBody: any = null;
    let postedHeaders: any = null;

    apiClient.post = (async (path: string, body: any, config?: any) => {
      postedPath = path;
      postedBody = body;
      postedHeaders = config?.headers;
      return mockAuthResponse;
    }) as any;

    try {
      const result = await loginWithSSO('google', 'test-google-id-token', 5);

      assert.strictEqual(postedPath, '/auth/sso/login/');
      assert.strictEqual(postedBody.provider, 'google');
      assert.strictEqual(postedBody.id_token, 'test-google-id-token');
      assert.strictEqual(postedHeaders['X-Foundation-ID'], '5');

      assert.strictEqual(result.access, 'sso-access-token-999');
      assert.strictEqual(result.user.full_name, 'Guru Google');

      // Verify tokens stored
      const storedAccess = await getItem(StorageKeys.ACCESS_TOKEN);
      const storedRefresh = await getItem(StorageKeys.REFRESH_TOKEN);
      assert.strictEqual(storedAccess, 'sso-access-token-999');
      assert.strictEqual(storedRefresh, 'sso-refresh-token-888');
    } finally {
      apiClient.post = originalPost;
    }
  });

  it('fetchSSOLinks calls GET /auth/sso/links/ and returns list', async () => {
    const mockLinks = [
      { provider: 'google', provider_user_id: 'g-12345', email: 'guru@sekolah.sch.id' },
    ];

    const originalGet = apiClient.get;
    apiClient.get = (async (path: string) => {
      assert.strictEqual(path, '/auth/sso/links/');
      return { data: { results: mockLinks }, status: 200 };
    }) as any;

    try {
      const links = await fetchSSOLinks();
      assert.strictEqual(links.length, 1);
      assert.strictEqual(links[0].provider, 'google');
      assert.strictEqual(links[0].email, 'guru@sekolah.sch.id');
    } finally {
      apiClient.get = originalGet;
    }
  });

  it('linkSSO posts provider and id_token to /auth/sso/link/', async () => {
    const originalPost = apiClient.post;
    apiClient.post = (async (path: string, body: any) => {
      assert.strictEqual(path, '/auth/sso/link/');
      assert.strictEqual(body.provider, 'microsoft');
      assert.strictEqual(body.id_token, 'ms-id-token-xyz');
      return {
        data: {
          provider: 'microsoft',
          provider_user_id: 'ms-oid-111',
          email: 'guru@outlook.com',
        },
        status: 201,
      };
    }) as any;

    try {
      const result = await linkSSO('microsoft', 'ms-id-token-xyz');
      assert.strictEqual(result.provider, 'microsoft');
      assert.strictEqual(result.provider_user_id, 'ms-oid-111');
      assert.strictEqual(result.email, 'guru@outlook.com');
    } finally {
      apiClient.post = originalPost;
    }
  });

  it('unlinkSSO sends DELETE to /auth/sso/link/?provider=<provider>', async () => {
    const originalDelete = apiClient.delete;
    let deletedPath = '';

    apiClient.delete = (async (path: string) => {
      deletedPath = path;
      return { status: 204, data: null };
    }) as any;

    try {
      await unlinkSSO('google');
      assert.strictEqual(deletedPath, '/auth/sso/link/?provider=google');
    } finally {
      apiClient.delete = originalDelete;
    }
  });

  it('signInWithProvider respects setMockProviderHandler when provided', async () => {
    setMockProviderHandler(async (provider) => `injected-token-for-${provider}`);

    const result = await signInWithProvider('google');
    assert.strictEqual(result.idToken, 'injected-token-for-google');
  });

  it('signInWithProvider provides default mock token fallback', async () => {
    const result = await signInWithProvider('microsoft');
    assert.ok(result.idToken.startsWith('mock-microsoft-id-token-'));
  });

  it('loginWithSSO propagates API error when account is not linked', async () => {
    const originalPost = apiClient.post;
    apiClient.post = (async () => {
      const error: any = new Error('Request failed with status code 404');
      error.response = {
        status: 404,
        data: { code: 'ACCOUNT_NOT_LINKED', error: 'No EduCore account linked.' },
      };
      throw error;
    }) as any;

    try {
      await assert.rejects(
        async () => {
          await loginWithSSO('google', 'unlinked-token');
        },
        (err: any) => {
          assert.strictEqual(err.response.data.code, 'ACCOUNT_NOT_LINKED');
          return true;
        }
      );
    } finally {
      apiClient.post = originalPost;
    }
  });
});
