/**
 * API base URL resolution: a build must name its server, and a wrong one must fail at startup.
 */
import { describe, it, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { ApiConfigError, devApiBase, resolveApiBase } from '../src/services/apiConfig.ts';
import { apiClient, getApiBaseUrl, setApiBaseUrl } from '../src/services/api.ts';

const prod = { isDev: false, platform: 'ios' };

describe('resolveApiBase', () => {
  it('uses the build-time env URL', () => {
    assert.strictEqual(
      resolveApiBase({ ...prod, envUrl: 'https://educore.makan.live/api/v1' }),
      'https://educore.makan.live/api/v1',
    );
  });

  it('prefers the env URL over the app-config extra', () => {
    assert.strictEqual(
      resolveApiBase({ ...prod, envUrl: 'https://a.example/api/v1', extraUrl: 'https://b.example/api/v1' }),
      'https://a.example/api/v1',
    );
  });

  it('falls back to the app-config extra when there is no env URL', () => {
    assert.strictEqual(resolveApiBase({ ...prod, extraUrl: 'https://b.example/api/v1' }), 'https://b.example/api/v1');
  });

  it('strips trailing slashes and whitespace', () => {
    assert.strictEqual(resolveApiBase({ ...prod, envUrl: '  https://a.example/api/v1//  ' }), 'https://a.example/api/v1');
  });

  it('accepts a host with a port', () => {
    assert.strictEqual(
      resolveApiBase({ isDev: true, platform: 'ios', envUrl: 'http://192.168.1.20:8000/api/v1' }),
      'http://192.168.1.20:8000/api/v1',
    );
  });

  describe('development fallback', () => {
    it('reaches the host from the Android emulator at 10.0.2.2', () => {
      assert.strictEqual(resolveApiBase({ isDev: true, platform: 'android' }), 'http://10.0.2.2:8000/api/v1');
    });

    it('reaches the host from the iOS simulator at localhost', () => {
      assert.strictEqual(resolveApiBase({ isDev: true, platform: 'ios' }), 'http://localhost:8000/api/v1');
      assert.strictEqual(devApiBase('ios'), 'http://localhost:8000/api/v1');
    });

    it('is never used by a release build', () => {
      assert.throws(() => resolveApiBase({ ...prod, platform: 'android' }), ApiConfigError);
    });
  });

  describe('rejects a bad URL', () => {
    it('plain http outside development', () => {
      assert.throws(
        () => resolveApiBase({ ...prod, envUrl: 'http://educore.makan.live/api/v1' }),
        /must use https/,
      );
    });

    it('a URL without the /api/v1 path, which would 404 every call', () => {
      assert.throws(() => resolveApiBase({ ...prod, envUrl: 'https://educore.makan.live' }), /must end with \/api\/v1/);
      assert.throws(
        () => resolveApiBase({ ...prod, envUrl: 'https://educore.makan.live/api/v1/api/v1/pos' }),
        /must end with \/api\/v1/,
      );
    });

    it('something that is not a URL', () => {
      for (const bad of ['educore.makan.live/api/v1', 'ftp://x.example/api/v1', 'https://', 'https://a b/api/v1']) {
        assert.throws(() => resolveApiBase({ ...prod, envUrl: bad }), /not a valid http\(s\) URL/, bad);
      }
    });

    it('a bad URL even in development, rather than guessing', () => {
      assert.throws(() => resolveApiBase({ isDev: true, platform: 'ios', envUrl: 'nope' }), ApiConfigError);
    });
  });
});

describe('apiClient base URL', () => {
  beforeEach(() => setApiBaseUrl(''));

  it('refuses to send a request before the base URL is set', async () => {
    let fetched = false;
    const originalFetch = globalThis.fetch;
    globalThis.fetch = (async () => {
      fetched = true;
      return new Response('{}');
    }) as typeof fetch;
    try {
      await assert.rejects(apiClient.get('/pos/sessions/'), ApiConfigError);
      assert.strictEqual(fetched, false);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it('joins the configured base and a relative path', async () => {
    setApiBaseUrl('https://educore.makan.live/api/v1/');
    assert.strictEqual(getApiBaseUrl(), 'https://educore.makan.live/api/v1');
    let seen = '';
    const originalFetch = globalThis.fetch;
    globalThis.fetch = (async (url: string) => {
      seen = url;
      return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
    }) as typeof fetch;
    try {
      await apiClient.get('/pos/sessions/');
      assert.strictEqual(seen, 'https://educore.makan.live/api/v1/pos/sessions/');
    } finally {
      globalThis.fetch = originalFetch;
    }
  });
});
