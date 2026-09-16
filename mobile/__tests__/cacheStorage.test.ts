import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { cacheGet, cacheSet } from '../src/services/storage.ts';

describe('Generic cache storage', () => {
  it('round-trips a cached value with a cachedAt timestamp', async () => {
    await cacheSet('test.cache.key', { hello: 'world' });
    const result = await cacheGet<{ hello: string }>('test.cache.key');
    assert.ok(result);
    assert.deepStrictEqual(result!.value, { hello: 'world' });
    assert.ok(typeof result!.cachedAt === 'string' && result!.cachedAt.length > 0);
  });

  it('returns null for a key that was never cached', async () => {
    const result = await cacheGet('test.cache.missing');
    assert.strictEqual(result, null);
  });
});
