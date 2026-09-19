/**
 * Pickup photo upload (spec/05 ATT-015): the two-phase upload against apps/core, the size guard, the direct PUT
 * without EduCore credentials, and that nothing about the photo is stored or logged by the client.
 */
import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { PICKUP_PHOTO_MAX_BYTES, PickupPhotoError, uploadPickupPhoto } from '../src/services/pickupPhoto.ts';
import { apiClient } from '../src/services/api.ts';

const photo = { uri: 'file:///cache/shrunk.jpg', contentType: 'image/jpeg' as const };

interface Recorded { url: string; init?: RequestInit }

function fakeFetch(opts: { bytes?: number; putStatus?: number; readFails?: boolean; putThrows?: boolean } = {}) {
  const calls: Recorded[] = [];
  const impl = (async (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    if (init?.method === 'PUT') {
      if (opts.putThrows) throw new Error('offline');
      return { ok: (opts.putStatus ?? 200) < 300, status: opts.putStatus ?? 200 } as Response;
    }
    if (opts.readFails) throw new Error('gone');
    return { blob: async () => new Blob([new Uint8Array(opts.bytes ?? 1000)]) } as unknown as Response;
  }) as unknown as typeof fetch;
  return { impl, calls };
}

async function withPosts<T>(run: (posts: Array<{ path: string; body?: unknown }>) => Promise<T>): Promise<T> {
  const original = apiClient.post;
  const posts: Array<{ path: string; body?: unknown }> = [];
  (apiClient as any).post = async (path: string, body?: unknown): Promise<any> => {
    posts.push({ path, body });
    const data = path === '/files/uploads/' ? { id: 42, key: 'STG/pickup_photo/abc_pickup.jpg', upload_url: 'https://storage.example/signed' } : {};
    return { data, status: 200, headers: {} };
  };
  try {
    return await run(posts);
  } finally {
    (apiClient as any).post = original;
  }
}

describe('uploadPickupPhoto', () => {
  it('initiates, PUTs the bytes to the signed URL, confirms, and returns the key', async () => {
    const { impl, calls } = fakeFetch({ bytes: 1234 });
    await withPosts(async (posts) => {
      const key = await uploadPickupPhoto(photo, impl);
      assert.strictEqual(key, 'STG/pickup_photo/abc_pickup.jpg');
      assert.deepStrictEqual(posts, [
        { path: '/files/uploads/', body: { purpose: 'pickup_photo', filename: 'pickup.jpg', content_type: 'image/jpeg', size: 1234 } },
        { path: '/files/uploads/42/confirm/', body: {} },
      ]);
    });
    const put = calls.find((c) => c.init?.method === 'PUT');
    assert.strictEqual(put?.url, 'https://storage.example/signed');
    assert.deepStrictEqual(put?.init?.headers, { 'Content-Type': 'image/jpeg' }); // no Authorization header
  });

  it('refuses a photo over the server limit without calling the API', async () => {
    const { impl } = fakeFetch({ bytes: PICKUP_PHOTO_MAX_BYTES + 1 });
    await withPosts(async (posts) => {
      await assert.rejects(uploadPickupPhoto(photo, impl), (e: unknown) => e instanceof PickupPhotoError && e.code === 'TOO_LARGE');
      assert.strictEqual(posts.length, 0);
    });
  });

  it('reports an unreadable local file', async () => {
    const { impl } = fakeFetch({ readFails: true });
    await withPosts(async (posts) => {
      await assert.rejects(uploadPickupPhoto(photo, impl), (e: unknown) => e instanceof PickupPhotoError && e.code === 'READ_FAILED');
      assert.strictEqual(posts.length, 0);
    });
  });

  it('does not confirm when the storage PUT fails, by status or by network error', async () => {
    for (const opts of [{ putStatus: 403 }, { putThrows: true }]) {
      const { impl } = fakeFetch(opts);
      await withPosts(async (posts) => {
        await assert.rejects(uploadPickupPhoto(photo, impl), (e: unknown) => e instanceof PickupPhotoError && e.code === 'UPLOAD_FAILED');
        assert.deepStrictEqual(posts.map((p) => p.path), ['/files/uploads/']);
      });
    }
  });

  it('the error carries a code only: no uri, no signed URL', async () => {
    const { impl } = fakeFetch({ putStatus: 500 });
    await withPosts(async () => {
      await assert.rejects(uploadPickupPhoto(photo, impl), (e: unknown) => {
        const text = JSON.stringify({ m: (e as Error).message, s: (e as Error).stack?.split('\n')[0] });
        return !text.includes('file://') && !text.includes('storage.example');
      });
    });
  });

  it('never logs or persists the photo', () => {
    for (const file of ['pickupPhoto.ts', 'photoPicker.ts']) {
      const source = readFileSync(new URL(`../src/services/${file}`, import.meta.url), 'utf8');
      assert.doesNotMatch(source, /console\.|from\s+['"][^'"]*storage['"]|\bAsyncStorage\b|\bsetItem\b|\bSecureStore\b/, file);
    }
  });
});
