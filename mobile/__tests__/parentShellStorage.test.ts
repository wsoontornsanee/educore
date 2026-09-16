import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { getLastChildId, saveLastChildId } from '../src/services/storage.ts';

describe('Last-selected child persistence (PAR-003)', () => {
  it('round-trips the last selected child id', async () => {
    await saveLastChildId(42);
    const result = await getLastChildId();
    assert.strictEqual(result, 42);
  });

  it('returns null when nothing was ever saved', async () => {
    const { removeItem, StorageKeys } = await import('../src/services/storage.ts');
    await removeItem(StorageKeys.LAST_CHILD_ID);
    const result = await getLastChildId();
    assert.strictEqual(result, null);
  });
});
